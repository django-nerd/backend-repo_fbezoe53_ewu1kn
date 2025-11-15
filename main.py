import os
import random
import string
from typing import List, Optional, Dict, Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from database import db, create_document, get_documents
from schemas import Card, GameRoom, PlayerModel, Rules

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ------------------ Helper functions ------------------
COLORS = ["red", "yellow", "green", "blue"]
VALUES = ["0","1","2","3","4","5","6","7","8","9","skip","reverse","draw2"]


def build_deck(rules: Rules) -> List[Card]:
    deck: List[Card] = []
    # number cards: one 0 per color, two each of 1-9
    for color in COLORS:
        deck.append(Card(color=color, value="0"))
        for v in ["1","2","3","4","5","6","7","8","9","skip","reverse","draw2"]:
            deck.append(Card(color=color, value=v))
            deck.append(Card(color=color, value=v))
    # wilds
    wild_count = 4
    deck += [Card(color="wild", value="wild") for _ in range(wild_count)]
    deck += [Card(color="wild", value="wild4") for _ in range(wild_count)]
    random.shuffle(deck)
    return deck


def new_room_code() -> str:
    return "".join(random.choices(string.ascii_uppercase, k=4))


def next_index(players_len: int, idx: int, direction: int) -> int:
    return (idx + direction) % players_len


# ------------------ Request models ------------------
class CreateRoomRequest(BaseModel):
    name: str
    rules: Optional[Rules] = None

class JoinRoomRequest(BaseModel):
    name: str

class PlayCardRequest(BaseModel):
    player_id: str
    card_index: Optional[int] = None
    chosen_color: Optional[str] = None
    say_uno: bool = False

class DrawRequest(BaseModel):
    player_id: str


# ------------------ Routes ------------------
@app.get("/")
def root():
    return {"message": "UNO backend ready"}

@app.get("/test")
def test_database():
    try:
        collections = db.list_collection_names() if db else []
        return {"backend": "ok", "db": bool(db), "collections": collections}
    except Exception as e:
        return {"backend": "ok", "db": False, "error": str(e)}

@app.post("/api/rooms/create")
def create_room(payload: CreateRoomRequest):
    rules = payload.rules or Rules()
    code = new_room_code()

    deck = build_deck(rules)
    # host player
    host_id = "p_" + os.urandom(4).hex()
    host = PlayerModel(player_id=host_id, name=payload.name, hand=[], is_host=True)
    # deal 7 cards to host initially
    for _ in range(7):
        host.hand.append(deck.pop())

    # start discard
    top = deck.pop()
    while top.color == "wild" and top.value in ("wild","wild4"):
        deck.append(top)
        random.shuffle(deck)
        top = deck.pop()

    room = GameRoom(
        code=code,
        players=[host],
        rules=rules,
        started=False,
        direction=1,
        current_player_index=0,
        draw_pile=deck,
        discard_pile=[top],
        current_color=top.color,
        pending_draw_count=0,
        pending_draw_type=None,
        winner_id=None,
    )

    create_document("gameroom", room.model_dump())
    return {"code": code, "player_id": host_id, "room": room}

@app.post("/api/rooms/{code}/join")
def join_room(code: str, payload: JoinRoomRequest):
    rooms = get_documents("gameroom", {"code": code}, limit=1)
    if not rooms:
        raise HTTPException(status_code=404, detail="Room not found")

    room = GameRoom(**{k: v for k, v in rooms[0].items() if k in GameRoom.model_fields})

    if any(p.player_id for p in room.players if p.name == payload.name):
        raise HTTPException(status_code=400, detail="Name already taken in room")

    # draw 7
    new_id = "p_" + os.urandom(4).hex()
    player = PlayerModel(player_id=new_id, name=payload.name, hand=[], is_host=False)
    for _ in range(7):
        if not room.draw_pile:
            # reshuffle
            top = room.discard_pile[-1]
            pile = room.discard_pile[:-1]
            random.shuffle(pile)
            room.draw_pile = pile
            room.discard_pile = [top]
        player.hand.append(room.draw_pile.pop())

    room.players.append(player)

    # persist update
    db["gameroom"].update_one({"code": code}, {"$set": room.model_dump()})
    return {"player_id": new_id, "room": room}

@app.get("/api/rooms/{code}")
def get_room(code: str):
    rooms = get_documents("gameroom", {"code": code}, limit=1)
    if not rooms:
        raise HTTPException(status_code=404, detail="Room not found")
    room = GameRoom(**{k: v for k, v in rooms[0].items() if k in GameRoom.model_fields})
    return room

@app.post("/api/rooms/{code}/start")
def start_room(code: str, player_id: str):
    rooms = get_documents("gameroom", {"code": code}, limit=1)
    if not rooms:
        raise HTTPException(status_code=404, detail="Room not found")
    room = GameRoom(**{k: v for k, v in rooms[0].items() if k in GameRoom.model_fields})

    host = next((p for p in room.players if p.is_host), None)
    if not host or host.player_id != player_id:
        raise HTTPException(status_code=403, detail="Only host can start")

    room.started = True
    db["gameroom"].update_one({"code": code}, {"$set": room.model_dump()})
    return room


def can_play(card: Card, top: Card, current_color: Optional[str]) -> bool:
    if card.color == "wild":
        return True
    if top.color == "wild":
        return card.color == current_color
    return card.color == top.color or card.value == top.value


@app.post("/api/rooms/{code}/play")
def play_card(code: str, payload: PlayCardRequest):
    rooms = get_documents("gameroom", {"code": code}, limit=1)
    if not rooms:
        raise HTTPException(status_code=404, detail="Room not found")
    room = GameRoom(**{k: v for k, v in rooms[0].items() if k in GameRoom.model_fields})

    if room.winner_id:
        return room

    player_idx = room.current_player_index
    player = room.players[player_idx]
    if payload.player_id != player.player_id:
        raise HTTPException(status_code=400, detail="Not your turn")

    # draw if requested
    if payload.card_index is None:
        # draw one
        if not room.draw_pile:
            top = room.discard_pile[-1]
            pile = room.discard_pile[:-1]
            random.shuffle(pile)
            room.draw_pile = pile
            room.discard_pile = [top]
        drawn = room.draw_pile.pop()
        player.hand.append(drawn)
        db["gameroom"].update_one({"code": code}, {"$set": room.model_dump()})
        return room

    if payload.card_index < 0 or payload.card_index >= len(player.hand):
        raise HTTPException(status_code=400, detail="Invalid card index")

    card = player.hand[payload.card_index]
    top = room.discard_pile[-1]

    if not can_play(card, top, room.current_color):
        raise HTTPException(status_code=400, detail="Card cannot be played")

    # play
    played = player.hand.pop(payload.card_index)
    room.discard_pile.append(played)

    # set color if wild
    if played.color == "wild":
        if not payload.chosen_color or payload.chosen_color not in COLORS:
            raise HTTPException(status_code=400, detail="Choose a valid color for wild")
        room.current_color = payload.chosen_color
    else:
        room.current_color = played.color

    # action effects
    step = 1
    if played.value == "reverse":
        room.direction *= -1
        if len(room.players) == 2:
            step = 0  # acts like skip in 2-player
    elif played.value == "skip":
        step = 2
    elif played.value == "draw2":
        target_idx = next_index(len(room.players), player_idx, room.direction)
        for _ in range(2):
            if not room.draw_pile:
                t = room.discard_pile[-1]
                pile = room.discard_pile[:-1]
                random.shuffle(pile)
                room.draw_pile = pile
                room.discard_pile = [t]
            room.players[target_idx].hand.append(room.draw_pile.pop())
        step = 2
    elif played.value == "wild4":
        target_idx = next_index(len(room.players), player_idx, room.direction)
        for _ in range(4):
            if not room.draw_pile:
                t = room.discard_pile[-1]
                pile = room.discard_pile[:-1]
                random.shuffle(pile)
                room.draw_pile = pile
                room.discard_pile = [t]
            room.players[target_idx].hand.append(room.draw_pile.pop())
        step = 2

    # check win
    if len(player.hand) == 0:
        room.winner_id = player.player_id

    # next turn
    if step:
        for _ in range(step):
            player_idx = next_index(len(room.players), player_idx, room.direction)
        room.current_player_index = player_idx

    db["gameroom"].update_one({"code": code}, {"$set": room.model_dump()})
    return room


@app.post("/api/rooms/{code}/draw")
def draw_card(code: str, payload: DrawRequest):
    rooms = get_documents("gameroom", {"code": code}, limit=1)
    if not rooms:
        raise HTTPException(status_code=404, detail="Room not found")
    room = GameRoom(**{k: v for k, v in rooms[0].items() if k in GameRoom.model_fields})

    player = room.players[room.current_player_index]
    if payload.player_id != player.player_id:
        raise HTTPException(status_code=400, detail="Not your turn")

    if not room.draw_pile:
        top = room.discard_pile[-1]
        pile = room.discard_pile[:-1]
        random.shuffle(pile)
        room.draw_pile = pile
        room.discard_pile = [top]

    drawn = room.draw_pile.pop()
    player.hand.append(drawn)

    # move turn
    room.current_player_index = next_index(len(room.players), room.current_player_index, room.direction)

    db["gameroom"].update_one({"code": code}, {"$set": room.model_dump()})
    return room


@app.post("/api/rooms/{code}/rules")
def set_rules(code: str, rules: Rules):
    rooms = get_documents("gameroom", {"code": code}, limit=1)
    if not rooms:
        raise HTTPException(status_code=404, detail="Room not found")
    room = GameRoom(**{k: v for k, v in rooms[0].items() if k in GameRoom.model_fields})

    if room.started:
        raise HTTPException(status_code=400, detail="Can't change rules after start")

    room.rules = rules
    db["gameroom"].update_one({"code": code}, {"$set": room.model_dump()})
    return room


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

"""
Database Schemas

UNO Multiplayer Game schemas using Pydantic models.
Each Pydantic model corresponds to a MongoDB collection (lowercased class name).
"""

from pydantic import BaseModel, Field
from typing import List, Literal, Optional, Dict, Any

Color = Literal["red", "yellow", "green", "blue", "wild"]
Value = Literal[
    "0","1","2","3","4","5","6","7","8","9",
    "skip","reverse","draw2","wild","wild4"
]

class Card(BaseModel):
    color: Color
    value: Value

class PlayerModel(BaseModel):
    player_id: str = Field(..., description="Unique ID for the player in a room")
    name: str
    hand: List[Card] = []
    is_host: bool = False

class Rules(BaseModel):
    version: Literal["classic", "party"] = "classic"
    stacking: bool = False
    seven_o: bool = False
    jump_in: bool = False

class GameRoom(BaseModel):
    code: str = Field(..., description="Room code, e.g., ABCD")
    players: List[PlayerModel] = []
    rules: Rules = Rules()
    started: bool = False
    direction: int = 1  # 1 clockwise, -1 counterclockwise
    current_player_index: int = 0
    draw_pile: List[Card] = []
    discard_pile: List[Card] = []
    current_color: Optional[Color] = None
    pending_draw_count: int = 0
    pending_draw_type: Optional[Literal["draw2","wild4"]] = None
    winner_id: Optional[str] = None

# Note: The app uses these models for validation and the database helper functions
# from `database.py` to persist and query data.

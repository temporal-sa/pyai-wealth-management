from dataclasses import dataclass
from typing import Optional

from pydantic import BaseModel

class ProcessUserMessageInput(BaseModel):
    user_input: str


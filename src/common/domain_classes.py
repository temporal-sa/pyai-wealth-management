from dataclasses import dataclass
from typing import Optional

@dataclass
class OpenInvestmentAccountInput:
    client_id: str
    account_name: str
    initial_amount: float

@dataclass
class OpenInvestmentAccountOutput:
    account_created: bool = False
    message : str = None

@dataclass
class WealthManagementClient:
    client_id: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    marital_status: Optional[str] = None
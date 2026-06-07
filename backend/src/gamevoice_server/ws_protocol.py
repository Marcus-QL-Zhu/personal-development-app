from dataclasses import dataclass


@dataclass(frozen=True)
class ProtocolMessage:
    type: str
    table_id: str
    payload: dict


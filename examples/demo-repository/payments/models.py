"""Core domain models for the payments demo."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class InvoiceStatus(str, Enum):
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"


class PaymentStatus(str, Enum):
    CREATED = "created"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass
class User:
    id: int
    email: str
    api_key: str = ""


@dataclass
class Invoice:
    id: int
    amount_cents: int
    status: InvoiceStatus = InvoiceStatus.PENDING


@dataclass
class Payment:
    id: int
    invoice_id: int
    amount_cents: int
    status: PaymentStatus = PaymentStatus.CREATED
    attempts: int = 0


@dataclass
class RetryResult:
    payment: Payment
    succeeded: bool
    attempts: int
    error: str | None = None
    events: list[str] = field(default_factory=list)

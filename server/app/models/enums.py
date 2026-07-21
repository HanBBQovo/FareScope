from enum import StrEnum


class UserRole(StrEnum):
    ADMIN = "admin"
    MEMBER = "member"


class UserStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    DISABLED = "disabled"


class TripType(StrEnum):
    ONE_WAY = "one_way"
    ROUND_TRIP = "round_trip"


class CollectionStatus(StrEnum):
    PENDING = "pending"
    LEASED = "leased"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


class NotificationChannelType(StrEnum):
    EMAIL = "email"
    TELEGRAM = "telegram"
    BARK = "bark"
    PUSHPLUS = "pushplus"
    WEBHOOK = "webhook"


class AlertRuleType(StrEnum):
    PRICE_THRESHOLD = "price_threshold"
    ABSOLUTE_DROP = "absolute_drop"
    PERCENTAGE_DROP = "percentage_drop"
    NEW_LOW = "new_low"
    DIRECT_AVAILABLE = "direct_available"
    MATCHING_ITINERARY = "matching_itinerary"
    ROUND_TRIP_RANGE = "round_trip_range"
    DATA_STALE = "data_stale"
    SCHEMA_DRIFT = "schema_drift"


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    SENDING = "sending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SUPPRESSED = "suppressed"

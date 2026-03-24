"""Constants for the Launtel Autoscaler integration."""

DOMAIN = "launtel_autoscaler"
MANUFACTURER = "Launtel"

# ── Config Keys ─────────────────────────────────────────────────────
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_SERVICE_ID = "service_id"
CONF_SCAN_INTERVAL = "scan_interval"

# ── Autoscaler Config ───────────────────────────────────────────────
CONF_ENABLED = "autoscaler_enabled"
CONF_WAN_SENSOR = "wan_utilisation_sensor"
CONF_UPGRADE_THRESHOLD = "upgrade_threshold_percent"
CONF_DOWNGRADE_THRESHOLD = "downgrade_threshold_percent"
CONF_UPGRADE_SUSTAINED_MINS = "upgrade_sustained_minutes"
CONF_DOWNGRADE_SUSTAINED_MINS = "downgrade_sustained_minutes"
CONF_MIN_TIER = "minimum_tier"
CONF_MAX_TIER = "maximum_tier"
CONF_COOLDOWN_MINS = "cooldown_minutes"
CONF_SCHEDULE = "schedule"

# ── Defaults ────────────────────────────────────────────────────────
DEFAULT_SCAN_INTERVAL = 60  # seconds
DEFAULT_UPGRADE_THRESHOLD = 80  # percent
DEFAULT_DOWNGRADE_THRESHOLD = 30  # percent
DEFAULT_UPGRADE_SUSTAINED = 10  # minutes
DEFAULT_DOWNGRADE_SUSTAINED = 30  # minutes
DEFAULT_MIN_TIER = "100_20"
DEFAULT_MAX_TIER = "1000_50"
DEFAULT_COOLDOWN = 15  # minutes

# ── Services ────────────────────────────────────────────────────────
SERVICE_CHANGE_SPEED = "change_speed"
SERVICE_PAUSE = "pause_service"
SERVICE_UNPAUSE = "unpause_service"
SERVICE_SET_AUTOSCALE = "set_autoscale"

# ── Tier ordering (for scale-up / scale-down logic) ─────────────────
TIER_ORDER = [
    "standby",
    "25_5",
    "50_20",
    "100_20",
    "100_40",
    "250_25",
    "250_100",
    "400_50",
    "500_200",
    "1000_50",
    "1000_400",
]

# ── Platforms ───────────────────────────────────────────────────────
PLATFORMS = ["sensor", "switch", "select"]

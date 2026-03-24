# Launtel Autoscaler for Home Assistant

A Home Assistant custom integration that monitors your WAN utilisation (via UniFi or any other source) and **automatically adjusts your Launtel NBN speed tier** to match demand — saving money when idle, scaling up when you need it.

## How It Works

```
┌─────────────┐     polls      ┌──────────────────┐
│  UniFi / HA  │ ──────────▸   │   Autoscaler     │
│  WAN Sensor  │               │   Engine         │
└─────────────┘               │                  │
                               │  if >80% for 10m │
                               │    → scale UP    │
                               │                  │
                               │  if <30% for 30m │
                               │    → scale DOWN  │
                               └────────┬─────────┘
                                        │
                                        ▼
                               ┌──────────────────┐
                               │  Launtel Portal   │
                               │  API              │
                               │  (change speed)   │
                               └──────────────────┘
                                        │
                                        ▼
                               ┌──────────────────┐
                               │  NBN processes    │
                               │  (1–5 min usual)  │
                               └──────────────────┘
```

Launtel charges daily and lets you change speed tiers on the fly. This integration takes advantage of that by keeping you on a cheap tier when idle and automatically upgrading when demand spikes.

## Features

- **Automatic scaling** — Monitors WAN utilisation and adjusts Launtel speed tier
- **Manual control** — Dropdown selector to pick a tier from the HA UI
- **Time schedules** — Boost for gaming nights, drop for sleeping hours
- **Away mode** — Drop to minimum when nobody's home
- **Budget protection** — Set min/max tiers and get cost alerts
- **Cooldown logic** — Prevents flapping between tiers
- **Full HA integration** — Sensors, switches, services, events, automations

## Requirements

- **Home Assistant** 2024.1+
- **Launtel** account with an active NBN service
- **WAN utilisation sensor** — from UniFi integration, SNMP, or any other source
- **HACS** (recommended) or manual installation

## Installation

### Via HACS (Recommended)

1. Open HACS in Home Assistant
2. Click the three dots → **Custom repositories**
3. Add the repository URL and select **Integration**
4. Search for "Launtel Autoscaler" and install
5. Restart Home Assistant

### Manual

1. Copy the `custom_components/launtel_autoscaler/` folder into your HA `config/custom_components/` directory
2. Restart Home Assistant

## Setup

### Step 1: Add the Integration

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **Launtel Autoscaler**
3. Enter your Launtel portal credentials (same as residential.launtel.net.au)
4. Select your service if you have multiple

### Step 2: Create a WAN Utilisation Sensor

The autoscaler needs a sensor that reports WAN utilisation as a **percentage (0–100)**. If you use the UniFi integration, add this template sensor to your `configuration.yaml`:

```yaml
template:
  - sensor:
      - name: "WAN Utilisation Percent"
        unique_id: wan_utilisation_percent
        unit_of_measurement: "%"
        state_class: measurement
        state: >
          {% set wan_mbps = states('sensor.udm_wan_download') | float(0) %}
          {% set tier_mbps = states('sensor.launtel_download_speed') | float(100) %}
          {% if tier_mbps > 0 %}
            {{ ((wan_mbps / tier_mbps) * 100) | round(1) }}
          {% else %}
            0
          {% endif %}
```

> **Note**: Replace `sensor.udm_wan_download` with whatever entity your UniFi gateway exposes for WAN download throughput. The exact entity name depends on your hardware (UDM, UDM Pro, USG, etc.).

### Step 3: Configure the Autoscaler

1. Go to the integration's **Configure** (options) page
2. Enable the autoscaler
3. Set the WAN sensor entity ID (e.g. `sensor.wan_utilisation_percent`)
4. Configure thresholds:

| Setting | Default | Description |
|---------|---------|-------------|
| Upgrade threshold | 80% | Sustained utilisation above this triggers scale-up |
| Downgrade threshold | 30% | Sustained utilisation below this triggers scale-down |
| Upgrade sustained | 10 min | How long usage must be high before scaling up |
| Downgrade sustained | 30 min | How long usage must be low before scaling down |
| Minimum tier | 100/20 | Floor — autoscaler won't go below this |
| Maximum tier | 1000/50 | Ceiling — autoscaler won't go above this |
| Cooldown | 15 min | Minimum time between tier changes |

## Entities

| Entity | Type | Description |
|--------|------|-------------|
| `sensor.launtel_current_tier` | Sensor | Current speed tier name |
| `sensor.launtel_download_speed` | Sensor | Download speed (Mbps) |
| `sensor.launtel_upload_speed` | Sensor | Upload speed (Mbps) |
| `sensor.launtel_daily_cost` | Sensor | Daily cost (AUD) |
| `sensor.launtel_service_status` | Sensor | Service status (active/paused) |
| `sensor.wan_utilisation_autoscaler` | Sensor | Current WAN % as seen by engine |
| `sensor.launtel_last_scale_event` | Sensor | Last autoscale event details |
| `switch.launtel_autoscaler` | Switch | Toggle autoscaling on/off |
| `select.launtel_speed_tier` | Select | Manual tier picker dropdown |

## Services

### `launtel_autoscaler.change_speed`

Manually change the speed tier.

```yaml
service: launtel_autoscaler.change_speed
data:
  tier_id: "250_25"
```

Available `tier_id` values: `standby`, `25_5`, `50_20`, `100_20`, `100_40`, `250_25`, `250_100`, `400_50`, `500_200`, `1000_50`, `1000_400`

### `launtel_autoscaler.set_autoscale`

Update autoscaler settings at runtime.

```yaml
service: launtel_autoscaler.set_autoscale
data:
  enabled: true
  upgrade_threshold: 85
  downgrade_threshold: 25
  min_tier: "50_20"
  max_tier: "400_50"
```

## Events

The integration fires `launtel_autoscaler_speed_changed` events that you can use in automations:

```yaml
automation:
  - trigger:
      - platform: event
        event_type: launtel_autoscaler_speed_changed
    action:
      - service: notify.mobile_app_your_phone
        data:
          message: >
            Speed {{ trigger.event.data.direction }}:
            {{ trigger.event.data.from_tier }} → {{ trigger.event.data.to_tier }}
```

## Example Automations

See `examples/configuration.yaml` for ready-to-use automations including:

- **Notifications** on every speed change
- **Gaming night boost** (Fri–Sun 7pm → 1Gbps)
- **Away mode** (drop to 25/5 when nobody's home)
- **WFH schedule** (higher upload tier on weekday mornings)
- **Budget alerts** (warn if daily cost stays high)
- **Dashboard card** config for Lovelace

## Important Notes

### NBN Speed Change Delays

Speed changes go through NBN Co's systems. While they usually complete in **1–5 minutes**, the NBN SLA allows up to **48 hours**. The autoscaler accounts for this with its cooldown timer, but be aware that changes aren't instant.

### Launtel Portal API

This integration uses Launtel's residential portal endpoints, which are **not an official public API**. Launtel has been supportive of community automation (see the launtsched project), but:

- The endpoints could change without notice
- Don't hammer the API — the default 60-second poll interval is reasonable
- If something breaks, check for portal updates

### Cost Awareness

The autoscaler is designed to **save you money**, but misconfiguration could increase costs. The budget alert automation and min/max tier limits exist for this reason. Launtel charges for the **highest tier active on any given day**, so even a brief spike to 1Gbps means you pay the 1Gbps rate for that calendar day.

### Daily Billing Cutoff

Launtel's daily billing resets at midnight AEST. If you're scheduling tier changes, keep this in mind — a change at 11:55pm still counts for that day.

## Troubleshooting

**"Speed change failed"** — Check your Launtel credentials haven't expired. The integration re-authenticates every 4 hours, but password changes require reconfiguration.

**"Tier not available at this address"** — Not all speed tiers are available at all addresses (depends on your NBN connection type: FTTP, HFC, FTTN, etc.). Check what tiers show in your Launtel portal.

**Slow speed changes** — This is an NBN issue, not Launtel or this integration. During peak times, NBN can take longer to process tier changes.

**WAN sensor shows 0** — Make sure your UniFi integration is working and the correct entity ID is configured. Check the entity in Developer Tools → States.

## License

MIT — Use at your own risk. This is not affiliated with or endorsed by Launtel.

# API: Time Measurement (Relay Action Time)

**Module Covered**: `time_measurement`

The Time Measurement test is used to measure the precise action time of a relay. It uses the FPGA's hardware SOE (Sequence of Events) timestamps to ensure microsecond-level accuracy. The backend manages a 3-node sequence (State 1, State 2, State 3) and up to 3 independent hardware-backed timers.

## 1. Start Command Payload

```json
{
  "cmd": "start",
  "module": "time_measurement",
  "params": {
    "sys": {
      "debounce": 15
    },
    "limits": {
      "0": [100.0, 60.0]
    },
    "statics": {
      "1": { "0": {"0": [57.735, 50.0]} },
      "2": { "0": {"0": [10.0, 50.0]} },
      "3": { "0": {"0": [57.735, 50.0]} }
    },
    "timers": {
      "timer1": {
        "start_mode": "ENTER_STATE_2",
        "stop_mode": "DI_A_CLOSE"
      },
      "timer2": {
        "start_mode": "TIMER1_STOP_START",
        "stop_mode": "DI_A_OPEN"
      },
      "timer3": {
        "start_mode": "DI_B_CLOSE_START",
        "stop_mode": "DI_C_OPEN"
      }
    },
    "nodes": [
      {
        "id": 1,
        "do_actions": [],
        "triggers": [
          {"condition": "TIME_OUT", "timeout": 2000, "next_id": 2}
        ]
      },
      {
        "id": 2,
        "do_actions": [],
        "triggers": [
          {"condition": "TIMER_1_2", "next_id": 3},
          {"condition": "TIME_OUT", "timeout": 5000, "next_id": 3}
        ]
      },
      {
        "id": 3,
        "do_actions": [],
        "triggers": [
          {"condition": "TIME_OUT", "timeout": 1000, "next_id": -2}
        ]
      }
    ]
  }
}
```

### Parameter Definitions

#### `sys` & `statics`
- `debounce`: DI debounce time (ms).
- `statics`: A dictionary mapping Node ID (string `"1"`, `"2"`, `"3"`) to the static physical values that should be output when that node is entered.

#### `timers`
You can configure up to 3 independent timers (`timer1`, `timer2`, `timer3`).
| Parameter | Description |
| :--- | :--- |
| `start_mode` | Defines what starts the timer. Options:<br> - `"ENTER_STATE_2"`: Starts exactly when the sequence enters State 2.<br> - `"TIMER1_STOP_START"`: Starts exactly when timer1 stops (cascade).<br> - `"DI_{PORT}_{EDGE}_START"`: Starts on a DI hardware edge. Ports: `A,B,C,R,a,b,c,r`. Edges: `CLOSE`, `OPEN`. (Example: `"DI_A_CLOSE_START"`) |
| `stop_mode` | Defines what stops the timer. Options:<br> - `"DI_{PORT}_{EDGE}"`: Stops on a DI hardware edge. (Example: `"DI_B_OPEN"`) |

#### `nodes`
Defines the FSM graph. Usually 3 nodes: Pre-Fault, Fault, Post-Fault.
| Parameter | Type | Description |
| :--- | :--- | :--- |
| `id` | int | Frontend identifier (1, 2, 3). |
| `do_actions` | list | Example: `[{"delay": 0, "mask": 3}]` |
| `triggers` | list | Condition to jump to `next_id`. Standard triggers (`TIME_OUT`, `DI_MATCH`) are supported. |

**State 2 Special Triggers**:
In Node 2, you can use specialized timer-based conditions to end the fault state automatically as soon as specific timers finish:
- `"TIMER_1"`: Jumps when Timer 1 stops.
- `"TIMER_1_2"`: Jumps when both Timer 1 and Timer 2 stop.
- `"TIMER_1_2_3"`: Jumps when all three timers stop.
- `"TIME_CTRL"`: Never automatically jumps based on timers; relies solely on `TIME_OUT` or manual commands.

---

## 2. Telemetry & Reporting

### Final Report
When the sequence completes (jumps to `-2` or manual stop), the system returns the precise elapsed time for all configured timers. Time is reported in milliseconds with sub-millisecond precision.
If a timer never finished (e.g., the stop condition was never met before the test ended), its value will be `null`.

```json
{
  "type": "report",
  "module": "time_measurement",
  "data": {
    "timer1_elapsed_ms": 32.145,
    "timer2_elapsed_ms": 105.002,
    "timer3_elapsed_ms": null
  }
}
```

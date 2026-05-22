# API: Status Sequence Test

**Module Covered**: `universal_sequence`

The Status Sequence Test (also known as State Sequencer) allows the frontend to define an arbitrary graph of states (nodes). Each state can have its own voltage/current output (STATIC or SWEEP), DO actions, and trigger conditions (DI match, Timeout) that dictate transitions to other states.

## 1. Start Command Payload

To initiate a Status Sequence test, send:
```json
{
  "cmd": "start",
  "module": "universal_sequence",
  "params": {
    "sys": {
      "debounce": 61
    },
    "nodes": [
      {
        "id": 1,
        "mode": "STATIC",
        "static": {
          "0": {"0": [10.0, 50.0]}
        },
        "do_actions": [
          {"delay": 0, "mask": 3}
        ],
        "triggers": [
          {
            "condition": "TIME_OUT",
            "timeout": 2000,
            "next_id": 2
          }
        ]
      },
      {
        "id": 2,
        "mode": "SWEEP",
        "static": {
          "0": {"0": [10.0, 50.0]}
        },
        "dynamic": {
          "stepTime": 100,
          "count": 50,
          "steps": {
            "0": {"0": [0.1, 0.0]}
          }
        },
        "triggers": [
          {
            "condition": "DI_MATCH",
            "di_mask": 255,
            "timeout": 5000,
            "post_condition_delay_ms": 0,
            "next_id": -2
          },
          {
            "condition": "COUNT_OVER",
            "next_id": -2
          }
        ]
      }
    ]
  }
}
```

### Parameter Definitions

#### `sys` (System Configuration)
| Parameter | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `debounce` | int | 61 | DI debounce time filtering hardware noise. |

#### `nodes` (Array of States)
Each node represents a distinct state in the sequence.
| Parameter | Type | Description |
| :--- | :--- | :--- |
| `id` | int | The frontend identifier for this node (1-based index). |
| `mode` | str | `"STATIC"` (fixed output) or `"SWEEP"` (dynamic stepping output). |
| `static` | dict | The baseline values output upon entering the node. Format: `{"ChannelID": {"LayerID": [Amplitude, Frequency/Phase]}}`. |
| `dynamic` | dict | Only used if `mode == "SWEEP"`. Contains `stepTime` (ms), `count` (total steps), and `steps` (increment delta per step). |
| `do_actions` | list | List of Digital Output actions to execute when entering this node. Example: `[{"delay": 0, "mask": 255}]`. |
| `triggers` | list | Array of rules dictating when and where to jump out of this node. |
| `next_id` | int | Default fallback jump target if no triggers are specified (-2 means terminal/stop). |

#### `triggers` Configuration
The engine evaluates triggers sequentially. The first matched condition executes the jump.
| Parameter | Type | Description |
| :--- | :--- | :--- |
| `condition` | str | `"DI_MATCH"`, `"TIME_OUT"`, `"COUNT_OVER"`, or `"MANUAL_TRIG"`. |
| `next_id` | int | The node `id` to jump to when triggered. Use `-2` to terminate the test. |
| `di_mask` | int | (For `DI_MATCH`) The bitmask of DI channels to monitor. |
| `timeout` | int | (For `TIME_OUT`) Time in ms before triggering. (If used with `DI_MATCH`, acts as a safety timeout). |
| `post_condition_delay_ms` | int | Delay in ms to wait *after* the condition is met before actually jumping to `next_id`. Behind the scenes, the engine creates a "stub node" to hold the output during this delay. |

---

## 2. Interaction Commands (Mid-Test)

### `next_node`
Used when a node has a trigger with `condition: "MANUAL_TRIG"`. Sending this command forces the engine to jump to that trigger's `next_id`.
```json
{
  "cmd": "next_node"
}
```

---

## 3. Telemetry & Reporting

### Mid-Test Updates
The frontend will receive real-time updates containing the current Node ID and physical outputs:
```json
{
  "type": "value_update",
  "nodeId": 1,
  "static": {"0": {"0": [10.0, 50.0]}},
  "di": 0,
  "do": 3
}
```

### Final Report
When the sequence terminates (jumps to `-2` or via `stop` command), the `TestCtrl` sends a row-by-row report detailing exactly how long the system stayed in each node and what caused the exit.
```json
{
  "type": "report",
  "module": "universal_sequence",
  "data": {
    "rows": [
      {
        "id": 1,
        "duration_sec": 2.000123,
        "end_condition": "TIME_OUT",
        "diA": null,
        "diB": null,
        "diC": null,
        "diR": null,
        "dia": null,
        "dib": null,
        "dic": null,
        "dir": null
      },
      {
        "id": 2,
        "duration_sec": 0.453000,
        "end_condition": "DI_MATCH",
        "diA": 0.451000,
        "diB": null,
        ...
      }
    ]
  }
}
```
- **`duration_sec`**: Total precise time spent in the state.
- **`end_condition`**: The trigger rule that caused the node to exit (e.g., `"DI_MATCH"` or `"TIME_OUT"`).
- **`diA` ... `dir`**: Relative time (in seconds) from node entry until that specific DI channel flipped state. `null` means it did not change during this state.

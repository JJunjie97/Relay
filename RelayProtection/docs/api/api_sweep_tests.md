# API: Sweep Tests

**Modules Covered**: `ac_test`, `dc_test`, `harmonic_test`, `steps_gradient_test`, `acdc_test`

This document defines the JSON payload sent by the frontend to start and control the sweep tests in the V3 architecture. All of these tests share the same underlying engine (formerly `ACTestHdl`), which executes step-by-step increments/decrements on voltage and current channels.

## 1. Start Command Payload

To initiate a sweep test, send a command via WebSocket:
```json
{
  "cmd": "start",
  "module": "ac_test",
  "params": {
    "sys": {
      "mode": 1,
      "changeMode": 0,
      "returnMode": 0,
      "stepTime": 1000,
      "logicMask": 255,
      "doMask": 0,
      "doCtrlMask": 0,
      "debounce": 61
    },
    "statics": {
      "0": {"0": [10.0, 50.0]},
      "1": {"0": [0.0, 50.0]}
    },
    "steps": {
      "0": {"0": [0.5, 0.0]}
    },
    "count": 10,
    "limits": {
      "0": [100.0, 50.0]
    },
    "payload": {
      "enablePreTestReset": false,
      "preTestResetTime": 1000,
      "enableStepReset": false,
      "stepResetMode": 0,
      "stepResetTime": 100,
      "resetTableData": {
        "0": {"0": [0.0, 50.0]}
      }
    },
    "dcComp": {
      "tau": 50.0,
      "dt": 10.0,
      "gateChannel": 0,
      "gatePhase": 0.0
    },
    "prevStatics": {}
  }
}
```

### Parameter Definitions

#### `sys` (System Configuration)
| Parameter | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `mode` | int | 1 | **1 (Auto)**: Sweep executes automatically. **0 (Manual)**: Static output only; relies on `update_static` to change values. |
| `changeMode` | int | 0 | **0**: Terminate test when trigger condition met. **1**: Hold values when triggered, wait for reverse sweep. |
| `returnMode` | int | 0 | **0**: Reverse sweep stops on DI trigger. **1**: Reverse sweep ignores DI and always runs for full `count`. |
| `stepTime` | int | 1000 | The duration of each sweep step in microseconds. |
| `logicMask` | int | 255 | DI matching mask. Bits [0:7]: physical channel mask. Bit 8 (0x100): 1=AND logic, 0=OR logic. (Note: Reference and Polarity bits like 0x200 and 0x400 are internal to backend USEEngine and not sent from frontend). |
| `doMask` | int | 0 | DO state applied during pre-test reset and inter-step reset phases. |
| `doCtrlMask` | int | 0 | DO state applied specifically during normal output (action) sweep phases. |
| `debounce` | int | 61 | DI debounce time. |

#### `statics` & `steps`
Format: `{"ChannelID": {"LayerID": [Amplitude, Frequency/Phase]}}`
- **`statics`**: The baseline values output at tick 0. Layer "0" is Frequency (Hz), Layer "1" is Phase (Degrees). Amplitude is in Volts/Amps.
- **`steps`**: The delta increment added *per step*. For example, if `stepTime`=1000 and `count`=10, the engine adds this step delta every 1ms for 10ms.

#### `count`
- **`count`**: int. The total number of steps to execute.

#### `limits`
- **`limits`**: Format: `{"ChannelID": [MaxAmplitude, MaxFreq]}`. System-wide clamp to prevent over-voltage/current.

#### `payload` (Reset Configurations)
| Parameter | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `enablePreTestReset` | bool | false | If true, applies `resetTableData` values for `preTestResetTime` ms before the test begins. |
| `enableStepReset` | bool | false | If true, the system resets between *each* step execution. |
| `stepResetMode` | int | 0 | **0**: Single-way reset (Forward sweep only). **1**: Full-trip reset (Forward and Reverse sweeps). |
| `stepResetTime` | int | 100 | The duration (ms) of the inter-step reset. |
| `resetTableData` | dict | {} | The specific output voltages/currents to apply during any reset phase. |

#### `dcComp` (Optional DC Compensation)
If provided along with `prevStatics`, the engine calculates a decaying DC offset to smoothly transition the magnetic flux in inductive loads.
- **`tau`**: Time constant of the load.
- **`dt`**: Decay step interval.

---

## 2. Interaction Commands (Mid-Test)

### `update_static`
Only used when `sys.mode == 0` (Manual Mode). Instantly updates the outputs.
```json
{
  "cmd": "update_static",
  "static": {
    "0": {"0": [15.0, 50.0]}
  }
}
```

### `next_node`
Used exclusively for manual control scenarios. 
*(Note: For Auto Mode where `sys.changeMode == 1`, the reverse sweep is triggered **automatically** by the backend's Hot Update mechanism upon DI trip. The frontend does **not** need to send `next_node`.)*
```json
{
  "cmd": "next_node"
}
```

---

## 3. Telemetry & Reporting

### Mid-Test Updates
The frontend will receive real-time updates:
```json
{
  "type": "value_update",
  "static": {"0": {"0": [12.5, 50.0]}}
}
```

### Final Report
When the test concludes (stops or finishes return sweep), the `TestCtrl` sends a final report:
```json
{
  "type": "report",
  "module": "ac_test",
  "data": {
    "tripTime": 5.2,
    "tripValues": {
      "0": {"0": [12.6, 50.0]}
    },
    "returnTime": 3.1,
    "returnValues": {
      "0": {"0": [11.0, 50.0]}
    },
    "returnRatio": 0.873
  }
}
```
- `tripTime`: Time in ms from test start to DI trigger.
- `returnTime`: (Optional) Time from reverse sweep start to DI return trigger.
- `returnRatio`: `returnValues` / `tripValues`.

# API: Inverse Time Tests

**Modules Covered**: `inverse_time_it_test`, `inverse_time_vft_test`

Inverse time tests execute a series of test points (e.g., various fault currents or voltage/frequency points). For each point, the engine holds a pre-fault state for a specified duration, then jumps to a fault state and waits for a DI trigger. The engine processes all test points continuously without stopping.

---

## 1. Inverse Time I-t Test (`inverse_time_it_test`)

Simulates overcurrent faults. Supports directional and non-directional fault modeling.

### Start Command Payload
```json
{
  "cmd": "start",
  "module": "inverse_time_it_test",
  "params": {
    "sys": {
      "debounce": 15,
      "logicMask": 255
    },
    "statics": {
      "0": {"0": [57.735, 50.0]}
    },
    "payload": {
      "ratedFrequency": 50.0,
      "iBeforeFault": 0.0,
      "tBeforeFault": 1.0,
      "faultLimitTime": 60.0,
      "iDefinition": "FAULT_CURRENT",
      "faultType": "A_GROUND",
      "negativeSequenceChannel": "I2",
      "outputMode": {
        "voltage": "GROUP1_UABC",
        "current": "GROUP1_IABC"
      },
      "directionality": {
        "enabled": false,
        "lineAngle": 70.0,
        "preLoadAngle": 0.0,
        "vBeforeFault": 57.735,
        "vFault": 20.0,
        "ctPolarity": "TOWARD_COMPONENT"
      }
    },
    "testPoints": [
      {"id": 1, "value": 5.0},
      {"id": 2, "value": 10.0}
    ]
  }
}
```

### Parameter Definitions

#### `sys`
- `debounce`: DI debounce filter (ms).
- `logicMask`: DI channels to monitor for the trip signal.

#### `payload` (Fault Definition)
| Parameter | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `ratedFrequency` | float | 50.0 | System frequency (Hz). |
| `iBeforeFault` | float | 0.0 | Pre-fault load current (A). |
| `tBeforeFault` | float | 1.0 | Duration of pre-fault state (Seconds). |
| `faultLimitTime` | float | 60.0 | Max duration to wait for trip during fault (Seconds). |
| `faultType` | str | `"A_GROUND"` | Defines which phases are involved: `A_GROUND`, `B_GROUND`, `C_GROUND`, `AB_PHASE`, `BC_PHASE`, `CA_PHASE`, `ABC_PHASE`. |
| `iDefinition` | str | `"FAULT_CURRENT"` | How the `testPoints.value` is interpreted: `"FAULT_CURRENT"`, `"NEGATIVE_SEQUENCE"`, `"ZERO_SEQUENCE"`, `"SINGLE_PATH"`. |
| `negativeSequenceChannel` | str | `"I2"` | Used if `iDefinition` is neg-seq: `"I2"` or `"3I0"`. |
| `outputMode` | dict | | Maps the logical A/B/C to physical HW channels. `voltage` can be `"GROUP1_UABC"` (0,1,2) or `"GROUP2_UXYZ"` (3,4,5). `current` can be `"GROUP1_IABC"` (6,7,8) or `"GROUP2_IXYZ"` (9,10,11). |

#### `payload.directionality` (Optional Directional Overcurrent)
- `enabled`: Set to true to enable voltage collapse and precise angle simulation.
- `lineAngle` / `preLoadAngle`: Impedance angles (degrees).
- `vBeforeFault` / `vFault`: Pre-fault voltage and clamped fault voltage.
- `ctPolarity`: `"TOWARD_COMPONENT"` or `"TOWARD_BUSBAR"`.

#### `testPoints`
An array of objects `{"id": 1, "value": 5.0}`. `value` represents the fault current amplitude (A).

---

## 2. Inverse Time V/F-t Test (`inverse_time_vft_test`)

Simulates over/under voltage, over/under frequency, or V/Hz ratio faults.

### Start Command Payload
```json
{
  "cmd": "start",
  "module": "inverse_time_vft_test",
  "params": {
    "sys": {
      "debounce": 15,
      "logicMask": 255
    },
    "statics": {},
    "payload": {
      "vBeforeFault": 57.735,
      "fBeforeFault": 50.0,
      "tBeforeFault": 1.0,
      "faultLimitTime": 60.0,
      "testProject": "V_T",
      "vOutputMode": "UABC_POS",
      "angle": 0.0,
      "vbValue": 57.735,
      "fbValue": 50.0,
      "vfVariable": "VOLTAGE_AMPLITUDE_V"
    },
    "testPoints": [
      {"id": 1, "value": 65.0},
      {"id": 2, "value": 70.0}
    ]
  }
}
```

### Parameter Definitions

#### `payload` (Fault Definition)
| Parameter | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `testProject` | str | `"V_T"` | `"V_T"` (Voltage trip), `"F_T"` (Frequency trip), or `"VF_T"` (V/Hz trip). |
| `vOutputMode` | str | `"UA"` | Which voltage channels to apply the fault to: `"UA"`, `"UB"`, `"UC"`, `"UAB"`, `"UBC"`, `"UCA"`, `"UABC_POS"`, `"UABC_NEG"`, `"UABC_ZERO"`. |
| `angle` | float | 0.0 | Base phase angle offset (degrees). |
| `vBeforeFault` / `fBeforeFault` | float | 57.735 / 50.0 | Pre-fault baseline state. |
| `tBeforeFault` | float | 1.0 | Duration of pre-fault state (Seconds). |
| `faultLimitTime`| float | 60.0 | Max duration to wait for trip (Seconds). |
| `vfVariable` | str | | Used only if `testProject == "VF_T"`. Defines what changes to achieve the V/Hz ratio: `"VOLTAGE_AMPLITUDE_V"` (keep freq at `fbValue`, change voltage) or `"VOLTAGE_FREQUENCY_F"` (keep volt at `vbValue`, change freq). |
| `vbValue` / `fbValue` | float | | Anchor values used for calculating the V/Hz ratio. |

#### `testPoints`
An array of objects `{"id": 1, "value": 65.0}`. Depending on `testProject`, `value` is interpreted as Volts, Hz, or V/Hz ratio.

---

## 3. Telemetry & Reporting

### Point-by-Point Updates (Mid-Test)
When the relay trips for a specific test point, the backend sends an update immediately:
```json
{
  "module": "inverse_time_it_test",
  "type": "value_update",
  "id": 1,
  "currentI": 5.0,        // or "vOrFValue" for vft test
  "actualTime": 1.2345,   // Time in seconds to trip
  "di": 0,
  "do": 3
}
```

### Final Report
When all points are exhausted, a final summary table is emitted:
```json
{
  "type": "report",
  "module": "inverse_time_it_test",
  "data": {
    "rows": [
      {
        "id": 1,
        "currentI": 5.0,
        "actualTime": 1.2345
      },
      {
        "id": 2,
        "currentI": 10.0,
        "actualTime": 0.4501
      }
    ]
  }
}
```

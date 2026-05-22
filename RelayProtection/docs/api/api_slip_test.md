# API: Slip Test (df/dt, dv/dt)

**Module Covered**: `slip_test`

The Slip Test is used to test frequency slip (df/dt), voltage slip (dv/dt), and related lock-out features of relays. The backend engine automatically generates a 3-node cycle (Reset -> Sweep -> Wait) and handles search iterations or timing logic depending on the `testType`.

## 1. Start Command Payload

```json
{
  "cmd": "start",
  "module": "slip_test",
  "params": {
    "sys": {
      "debounce": 15,
      "logicMask": 255,
      "doMask": 0,
      "doCtrlMask": 0
    },
    "statics": {
      "0": {"0": [57.735, 50.0]}
    },
    "payload": {
      "testType": 1,
      "slipVarType": "0_0",
      "slipPhase": "0,1,2",
      "slipPhasor": 2,
      "slipStart": 50.0,
      "slipEnd": 45.0,
      "slipDDt": 1.0,
      "returnTime": 1000,
      "waitTime": 1000,
      "timerStartValue": 49.5
    },
    "limits": {
      "0": [100.0, 60.0]
    }
  }
}
```

### Parameter Definitions

#### `sys`
| Parameter | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `debounce` | int | 15 | DI debounce time (ms). |
| `logicMask` | int | 255 | DI channels to monitor for the trip signal. |
| `doMask` | int | 0 | DO state during Reset phase. |
| `doCtrlMask`| int | 0 | DO state during Sweep phase. |

#### `payload` (Core Parameters)
| Parameter | Type | Description |
| :--- | :--- | :--- |
| `testType` | int | Defines the search mode (see below). |
| `slipVarType` | str | `"2_0"` applies factor $1/\sqrt{3}$ for Line Voltage. `"5"` applies to all 12 channels. |
| `slipPhase` | str | Comma-separated channel IDs to sweep (e.g., `"0,1,2"`). |
| `slipPhasor` | int | **0**: Amplitude (dv/dt), **1**: Phase, **2**: Frequency (df/dt). |
| `slipStart` | float | Starting value of the sweep. |
| `slipEnd` | float | Default endpoint of the sweep. |
| `slipDDt` | float | Rate of change per second (e.g., df/dt in Hz/s). The backend calculates the step delta for an internal 10ms step interval. |
| `returnTime` | int | Time (ms) spent in the pre-fault Reset state (Node 0) before sweeping starts. |
| `waitTime` | int | Time (ms) spent holding the `slipEnd` value (Node 2) waiting for trip. |

#### Search / Timing Modes (`testType`)
The behavior of the test drastically changes based on `testType`. It activates specific payload parameters:

*   **`testType: 0` (Action Value Search)**
    *   Iterates the *endpoint* of the sweep to find the exact threshold where the relay trips.
    *   Uses: `actionSearchStart`, `actionSearchStep`, `actionSearchEnd`.
*   **`testType: 1` (Action Time Test)**
    *   Single sweep from `slipStart` to `slipEnd`. Measures the exact time taken to trip *starting from* a specific `timerStartValue` passing point.
    *   Uses: `timerStartValue`.
*   **`testType: 2` (Slip Lock Search)**
    *   Iterates the rate of change `slipDDt` to find the lock-out threshold (e.g., maximum df/dt tolerated before locking).
    *   Uses: `ddtSearchStart`, `ddtSearchStep`, `ddtSearchEnd`.
*   **`testType: 3` (Current Lock Search)**
    *   Sweeps `slipStart` to `slipEnd`, but iterates a static *Current* value on another channel to find the low-current lockout boundary.
    *   Uses: `lockPhase`, `lockSearchStart`, `lockSearchStep`, `lockSearchEnd`.
*   **`testType: 4` (Voltage Lock Search)**
    *   Similar to 3, but iterates a static *Voltage* value.
    *   Uses: `lockPhase`, `lockSearchStart`, `lockSearchStep`, `lockSearchEnd`.

---

## 2. FSM Execution Flow

The Slip Test automatically compiles a looping 3-Node FSM:
1.  **Node 0 (Reset):** Outputs `slipStart` and `statics` for `returnTime` ms.
2.  **Node 1 (Sweep):** Steps by `slipDDt / 100` every 10ms. If DI trips, it terminates. If it reaches the target, jumps to Node 2.
3.  **Node 2 (Wait):** Holds the target value for `waitTime` ms. If `testType` is a search mode (0, 2, 3, 4) and no trip occurred, it updates the search variable and *jumps back to Node 0*. If it's `testType=1`, it terminates.

---

## 3. Telemetry & Reporting

### Final Report
The report content depends on the `testType`.

**testType 0:**
```json
{
  "type": "report",
  "module": "slip_test",
  "data": {
    "testType": 0,
    "actionValue": 48.5  // The endpoint value that caused the trip
  }
}
```

**testType 1:**
```json
{
  "type": "report",
  "module": "slip_test",
  "data": {
    "testType": 1,
    "timerStartValue": 49.5,
    "actionTime": 150.2  // Time in ms from timerStartValue to Trip
  }
}
```

**testType 2:**
```json
{
  "type": "report",
  "module": "slip_test",
  "data": {
    "testType": 2,
    "slipLockValue": 2.5 // The max df/dt
  }
}
```

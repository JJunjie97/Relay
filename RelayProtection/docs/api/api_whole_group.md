# API: Whole Group Test (State Sequencer with Templates)

**Module Covered**: `whole_group_test`

The Whole Group Test evaluates the entire relay protection loop, including fault detection, trip outputs, and auto-reclosing. The backend auto-generates a complex FSM (up to 10 nodes) based on high-level templates and settings provided by the frontend.

## 1. Start Command Payload

```json
{
  "cmd": "start",
  "module": "whole_group_test",
  "params": {
    "sys": {
      "debounce": 15
    },
    "nodes": [
      {
        "id": 0,
        "static": {"0": {"0": [57.735, 50.0]}}
      },
      {
        "id": 1,
        "static": {"0": {"0": [20.0, 50.0]}}
      },
      {
        "id": 2,
        "static": {"0": {"0": [57.735, 50.0]}}
      }
    ],
    "payload": {
      "triggerMode": {
        "prefaultTrigger": {"condition": "MANUAL_TRIG"}
      },
      "faultSettings": {
        "testLimitMs": 5000,
        "faultNature": 0,
        "firstFault": {
          "type": 0
        },
        "transition": {
          "enabled": false,
          "transitionMoment": 0,
          "transitionTimeMs": 100
        }
      },
      "binaryInput": {
        "settings": [
          {"function": 0}, 
          {"function": 4}
        ]
      },
      "binaryOutput": {
        "controlMode": 0,
        "initialMask": 3,
        "delayReversal": {"delay": 100, "hold": 100},
        "doSequence": []
      },
      "uiOutput": {
        "uxAfterTrip": {"amplitude": 0.0, "angle": 0.0}
      },
      "testControl": {
        "dcComponent": {
          "enabled": false,
          "angle": 0.0,
          "tau": 0.04
        }
      }
    }
  }
}
```

### Parameter Definitions

#### `nodes` (Templates)
The frontend provides the base physical values for the 3 core states. The backend uses these to construct the full node graph.
- **Node 0**: Pre-fault normal state.
- **Node 1**: Fault state (e.g., low voltage, high current).
- **Node 2**: Conversion fault state (used if `transition.enabled = true`).

#### `payload.faultSettings`
| Parameter | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `faultNature` | int | 0 | **0**: Transient (Auto-reclose succeeds, stays normal). **1**: Permanent (Auto-reclose hits fault again, causes 2nd trip). |
| `firstFault.type`| int | 0 | **0-2**: A/B/C Ground. **3-5**: AB/BC/CA Phase. **6-8**: AB/BC/CA Ground. **9**: Three-phase short. (Dictates which DI pins the engine monitors for the trip signal). |
| `testLimitMs` | int | 5000 | Max time to wait in fault or reclose states before aborting. |
| `transition.enabled` | bool | false | Enable evolving fault. |
| `transition.transitionMoment` | int | 0 | **0**: Convert during initial fault. **1**: Convert during reclose fault. |

#### `payload.binaryInput.settings`
Maps the physical DI hardware pins (0-7) to logical functions.
- `function`: **0**: Trip A. **1**: Trip B. **2**: Trip C. **3**: Three-phase Trip. **4**: Reclose.

#### `payload.binaryOutput`
| Parameter | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `controlMode` | int | 0 | **0**: Sync with state (DO changes instantly on Trip/Reclose). **1**: Delay (DO changes after `delayReversal` time). **2**: Custom sequence. |
| `initialMask` | int | 3 | Base DO mask for normal state. |

#### `payload.uiOutput` & `testControl`
- `uxAfterTrip`: Overrides the Ux (Channel 3) voltage amplitude/angle when the relay is in the tripped (open) state.
- `dcComponent`: Injects a decaying DC offset to simulate transformer saturation or inductive load inertia at the moment of the fault.

---

## 2. FSM Execution Flow

The backend expands the 3 templates into a multi-node sequence:

1.  **Node 0**: Pre-fault. Waits for `prefaultTrigger` (e.g., `MANUAL_TRIG`).
2.  *(Optional)* **Node 1001**: DC Component decay phase.
3.  **Node 1**: Fault State. Engine outputs Node 1 template. Waits for DI Trip.
4.  *(Internal)* **Node 10**: Trip Delay (20ms debounce/settle).
5.  **Node 2**: Trip State (Breaker open). Engine clears all currents and applies `uxAfterTrip`. Waits for DI Reclose.
6.  *(Internal)* **Node 20**: Reclose Delay (20ms).
7.  **Node 3**: Reclose State. 
    - If `faultNature=0` (Transient), outputs Node 0 template.
    - If `faultNature=1` (Permanent), outputs Node 1 template and waits for 2nd DI Trip.
8.  *(Internal)* **Node 30**: Second Trip Delay.
9.  **Node 4**: Second Trip State. Outputs trip state.
10. *(Optional)* **Node 5 & 50**: Conversion Fault states if `transition` is enabled.

---

## 3. Telemetry & Reporting

### Mid-Test Updates
The frontend receives `value_update` messages whenever the backend jumps to a new logical node, allowing UI state machines to sync.

### Final Report
When the sequence terminates, the backend searches the exact hardware SOE (Sequence of Events) times for the trip and reclose DI pins. Time is reported in milliseconds.

```json
{
  "type": "report",
  "module": "whole_group_test",
  "data": {
    "rows": [
      {
        "tripA": 45.2,
        "tripB": null,
        "tripC": null,
        "recloseR": 503.1,
        "diA": 1,
        "diB": 0,
        "diC": 0,
        "diR": 1,
        "dia": 0, "dib": 0, "dic": 0, "dir": 0
      }
    ]
  }
}
```

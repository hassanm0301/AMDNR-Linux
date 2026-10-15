# Host resource regression checks

Run from the project root:

```sh
python3 main/AMDNR-source/tests/test_host_resources.py
```

The checks perform no GPU work and launch no game. A native C++ compiler runs
fence-completion cases (already complete, completion during a wait, incomplete
wait, failed registration, device removal and 64-bit values). If MinGW is
installed, the changed queue-drain and readback-allocation functions are also
compiled against its Windows and D3D12 declarations. The resize check ensures
failed synchronization exits before texture replacement or temporal preparation.

These are local modifications to the **0.3.4.2 corresponding-source snapshot**.
They do not modify or replace the packaged **0.3.5.5R4 OptiScaler.dll**. The full
host still needs a Windows/MSVC build and in-game validation before deployment;
passing these isolated checks does not constitute a full host build or prove an
FPS improvement.

The fixes preserve resources when a resize cannot confirm queue completion,
including a failed event registration or device removal, and avoid destroying
retired runtime sessions on a removed-device fence sentinel. Optional statistics
allocation errors now disable those statistics through the existing recovery
path. The CPU readback range covers both exposure values that are actually read.

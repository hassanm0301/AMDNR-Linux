#!/usr/bin/env python3
"""CPU-only host resource regression checks; never launches a game or GPU work.

Run with python3 main/AMDNR-source/tests/test_host_resources.py.
The full host still requires Visual Studio; MinGW checks the actual changed
resource functions separately against its Windows/D3D12 declarations.
"""
import pathlib
import shutil
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = ROOT / "optiscaler/dlssnr/lmxxf/LmxxfBackend.cpp"


def function_body(source, signature):
    start = source.index(signature)
    body = source.index("{", start)
    depth = 1
    end = body + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


class HostResourceTests(unittest.TestCase):
    def test_fence_completion_sequences(self):
        compiler = shutil.which("c++")
        if not compiler:
            self.skipTest("C++ compiler unavailable")
        with tempfile.TemporaryDirectory(prefix="amdnr-fence-test-") as directory:
            exe = pathlib.Path(directory) / "fence_wait_test"
            subprocess.run([compiler, "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror",
                            str(ROOT / "tests/fence_wait_test.cpp"), "-o", str(exe)], check=True)
            subprocess.run([str(exe)], check=True)

    def test_changed_functions_compile_with_windows_declarations(self):
        compiler = shutil.which("x86_64-w64-mingw32-g++")
        if not compiler:
            self.skipTest("MinGW Windows cross compiler unavailable")
        source = SOURCE.read_text()
        drain = function_body(source, "bool DrainQueue(bool atExit = false)")
        readback = function_body(source, "ComPtr<ID3D12Resource> ReadbackBuffer(")
        fixture = """
#include <d3d12.h>
#include <wrl/client.h>
#include <string>
#include "dlssnr/amd/FenceWait.h"
using Microsoft::WRL::ComPtr;
void Check(HRESULT, const char*);
struct Host {
    ComPtr<ID3D12Device> device;
    ComPtr<ID3D12CommandQueue> queue;
    ComPtr<ID3D12Fence> drainFence;
    UINT64 drainValue = 0;
    HANDLE drainEvent = nullptr;
    bool loggedDrain = false;
    void Log(const std::string&);
""" + drain + "\n};\n" + readback
        with tempfile.TemporaryDirectory(prefix="amdnr-host-compile-") as directory:
            path = pathlib.Path(directory) / "host_resources.cpp"
            path.write_text(fixture)
            subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-fsyntax-only",
                            "-I" + str(ROOT / "optiscaler"), str(path)], check=True)

    def test_resize_checks_drain_before_replacing_resources(self):
        source = SOURCE.read_text()
        record = function_body(source, "ID3D12Resource* Backend::Impl::Record(")
        resize = function_body(record, "if (resized)")
        guard = resize.index("else if (!DrainQueue())")
        failure = resize.index("throw std::runtime_error", guard)
        self.assertLess(resize.index("api.AbandonJob(ctx, job.handle)"), guard)
        self.assertLess(failure, resize.index("Bury(fed)"))
        self.assertLess(failure, resize.index("w = f.width"))
        self.assertLess(failure, resize.index("temporal->Prepare(w, h)"))

if __name__ == "__main__":
    unittest.main()

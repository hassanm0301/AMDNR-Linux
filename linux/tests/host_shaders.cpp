// SPDX-License-Identifier: GPL-3.0-or-later
#include "../../main/AMDNR-source/optiscaler/dlssnr/amd/TemporalStability.h"
#include <cstdio>

int main()
{
    ID3DBlob *code = nullptr, *errors = nullptr;
    HRESULT result = DlssNr::SysCompiler::Compile(AmdPreSr::TemporalStabilityShader,
        sizeof(AmdPreSr::TemporalStabilityShader), "AMD temporal stability", nullptr, nullptr,
        "main", "cs_5_0", 0, 0, &code, &errors);
    if (errors) { std::fprintf(stderr, "%s\n", static_cast<const char *>(errors->GetBufferPointer())); errors->Release(); }
    std::printf("Host shader compiler: %ls\n", DlssNr::SysCompiler::Get().path.c_str());
    if (FAILED(result)) { std::fprintf(stderr, "temporal shader: %#lx\n", (unsigned long)result); return 1; }
    std::printf("PASS: compiled host temporal shader (%zu bytes) through System32 compiler\n", code->GetBufferSize());
    code->Release();
    return 0;
}

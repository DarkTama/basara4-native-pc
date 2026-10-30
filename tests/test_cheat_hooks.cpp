#include <cassert>
#include "../src/cheat_hook.h"
#include <array>
#include <atomic>
#include <cstdio>
#include <vector>
#ifdef _WIN32
#include <windows.h>
#else
#include <sys/mman.h>
#endif

int main(int argc, char** argv) {
    unsigned char code[256]{};
    std::vector<unsigned char> image;
    if (argc > 1) {
        FILE* file = std::fopen(argv[1], "rb");
        assert(file && !std::fseek(file, 0, SEEK_END));
        const auto size = std::ftell(file);
        assert(size > 0x4000);
        image.resize(size - 0x4000);
        assert(!std::fseek(file, 0x4000, SEEK_SET));
        assert(std::fread(image.data(), 1, image.size(), file) == image.size());
        std::fclose(file);
    }
    for (unsigned i = 0; i < SPARTAN_CHEAT_HOOK_COUNT; ++i) {
        const auto& hook = spartanport_cheat_hooks[i];
        assert(spartanport_cheat_signature_match(&hook, hook.original, hook.length));
        code[0] = 0;
        assert(!spartanport_cheat_signature_match(&hook, code, hook.length));
        assert(!spartanport_cheat_signature_match(&hook, hook.original, hook.length - 1));
        assert(spartanport_cheat_build_stub(code, &hook, 0x12345678, 0x87654321) < sizeof(code));
        if (!image.empty()) {
            assert(hook.offset + hook.length <= image.size());
            assert(spartanport_cheat_signature_match(&hook, image.data() + hook.offset, image.size() - hook.offset));
        }
#ifdef _WIN32
        auto* executable = static_cast<unsigned char*>(VirtualAlloc(nullptr, 4096, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE));
#else
        auto* executable = static_cast<unsigned char*>(mmap(nullptr, 4096, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0));
        assert(executable != MAP_FAILED);
#endif
        assert(executable);

        std::vector<unsigned char> entry = {
            0x53, 0x57, 0x56, 0x41, 0x54, 0x41, 0x55, 0x41, 0x56, 0x41, 0x57  // push rbx, rdi, rsi, r12, r13, r14, r15
        };
        if (hook.id == BASARA4_CHEAT_GOLD) {
            entry.insert(entry.end(), {0x48, 0x89, 0xc8, 0x89, 0xd1}); // mov rax, rcx; mov ecx, edx
        } else if (hook.id == BASARA4_CHEAT_COINS) {
            entry.insert(entry.end(), {0x48, 0x89, 0xcf, 0x89, 0xd6}); // mov rdi, rcx; mov esi, edx
        } else if (hook.id == BASARA4_CHEAT_GROWTH_POINTS) {
            entry.insert(entry.end(), {0x49, 0x89, 0xcd, 0x89, 0xd0}); // mov r13, rcx; mov eax, edx
        } else if (hook.id == BASARA4_CHEAT_WEAPON_POINTS) {
            entry.insert(entry.end(), {0x89, 0xd0});                     // mov eax, edx (rcx is already buffer)
        } else if (hook.id == BASARA4_CHEAT_WEAPON_RANK) {
            entry.insert(entry.end(), {0x48, 0x89, 0xc8});               // mov rax, rcx (edx is already val)
        }
        entry.insert(entry.end(), {0xff, 0x25, 0, 0, 0, 0});             // jmp qword ptr [rip] -> stub

        memcpy(executable, entry.data(), entry.size());
        uintptr_t target = reinterpret_cast<uintptr_t>(executable + 64);
        memcpy(executable + entry.size(), &target, 8);

        std::atomic<bool> flag{false};
        assert(spartanport_cheat_build_stub(executable + 64, &hook, reinterpret_cast<uintptr_t>(&flag),
                                            reinterpret_cast<uintptr_t>(executable + 256)) < 192);

        const unsigned char exit[] = {0x41, 0x5f, 0x41, 0x5e, 0x41, 0x5d, 0x41, 0x5c, 0x5e, 0x5f, 0x5b, 0xc3};
        memcpy(executable + 256, exit, sizeof(exit));
#ifdef _WIN32
        DWORD previous;
        assert(VirtualProtect(executable, 4096, PAGE_EXECUTE_READ, &previous));
        FlushInstructionCache(GetCurrentProcess(), executable, 4096);
#else
        assert(!mprotect(executable, 4096, PROT_READ | PROT_EXEC));
#endif
        using Execute = void (*)(void*, uint32_t);
        const auto execute = reinterpret_cast<Execute>(executable);

        std::array<unsigned char, 0x800> buffer{};
        const unsigned offsets[] = {0xdc, 0x2e8, 0x6b4, 0x6b4, 0xe4, 0xe8};
        const unsigned offset = offsets[i];

        uint32_t init_val = (hook.id == BASARA4_CHEAT_GROWTH_POINTS) ? 5000 : 100;
        memcpy(buffer.data() + offset, &init_val, 4);

        // 1. Cheat Disabled
        flag.store(false);
        execute(buffer.data(), 50);
        uint32_t result;
        memcpy(&result, buffer.data() + offset, 4);
        if (hook.id == BASARA4_CHEAT_COINS) {
            assert(result == 150);
        } else if (hook.id == BASARA4_CHEAT_GROWTH_POINTS) {
            assert(result == 4950);
        } else {
            if (result != 50) {
                fprintf(stderr, "Hook %u (id %u) failed: expected 50, got %u\n", (unsigned)i, (unsigned)hook.id, result);
            }
            assert(result == 50);
        }

        // 2. Cheat Enabled
        flag.store(true);
        execute(buffer.data(), 50);
        memcpy(&result, buffer.data() + offset, 4);
        if (hook.id == BASARA4_CHEAT_GOLD) {
            assert(result == 0x09999999);
        } else if (hook.id == BASARA4_CHEAT_COINS) {
            assert(result == 999999);
        } else if (hook.id == BASARA4_CHEAT_GROWTH_POINTS) {
            assert(result == 0x09999999);
        } else if (hook.id == BASARA4_CHEAT_WEAPON_POINTS) {
            assert(result == 0x00099999);
        } else if (hook.id == BASARA4_CHEAT_WEAPON_RANK) {
            assert(result == 0x00000099);
        }

        // 3. Cheat Disabled Again
        flag.store(false);
        init_val = 200;
        memcpy(buffer.data() + offset, &init_val, 4);
        execute(buffer.data(), 25);
        memcpy(&result, buffer.data() + offset, 4);
        if (hook.id == BASARA4_CHEAT_COINS) {
            assert(result == 225);
        } else if (hook.id == BASARA4_CHEAT_GROWTH_POINTS) {
            assert(result == 175);
        } else {
            assert(result == 25);
        }

#ifdef _WIN32
        VirtualFree(executable, 0, MEM_RELEASE);
#else
        munmap(executable, 4096);
#endif
    }
    std::puts("cheat-hooks-test: OK (6 Basara 4 hooks, live toggles, registers, flags, signatures)");
    return 0;
}

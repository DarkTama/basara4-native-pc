// spartanport: GPU-side assertion failures stop the port with exit code 23.
#include <cstdio>
#include <cstdlib>
#include <stdexcept>
#include "common/assert.h"
#include "common/logging/log.h"

extern "C" void spartanport_write_minidump(void* exception_pointers);

void assert_fail_impl() {
    std::fflush(stdout);
    std::fputs("STOP: GPU library assertion failed (see GPU log above)\n", stderr);
#ifdef _WIN32
    spartanport_write_minidump(nullptr);
#endif
    std::_Exit(23);
}

[[noreturn]] void unreachable_impl() {
    assert_fail_impl();
    throw std::runtime_error("Unreachable code");
}

void assert_fail_debug_msg(const char* msg) {
    LOG_CRITICAL(Debug, "Assertion Failed!\n{}", msg);
    assert_fail_impl();
}

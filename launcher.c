/*
 * launcher.c — tiny Mach-O launch stub for iCloud Photo Storage Saver.
 *
 * The app's real launch logic lives in Resources/launch.sh (bash). A notarized
 * app's main executable must be a Mach-O so that the hardened runtime and the
 * code-signing entitlements actually attach to it — a shell script can't carry
 * them. This stub just locates and execs the bundled launch.sh.
 *
 *   cc -O2 -arch arm64 -o launcher launcher.c
 */
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <limits.h>
#include <libgen.h>
#include <mach-o/dyld.h>

int main(int argc, char *argv[]) {
    char exepath[PATH_MAX];
    uint32_t size = sizeof(exepath);
    if (_NSGetExecutablePath(exepath, &size) != 0) {
        fprintf(stderr, "launcher: could not resolve executable path\n");
        return 1;
    }
    /* dirname() may modify its argument; it points at .../Contents/MacOS */
    char *macos_dir = dirname(exepath);

    char script[PATH_MAX];
    snprintf(script, sizeof(script), "%s/../Resources/launch.sh", macos_dir);

    char *bash_argv[] = { "/bin/bash", script, NULL };
    execv("/bin/bash", bash_argv);

    /* Only reached if execv failed. */
    perror("launcher: execv");
    return 1;
}

/* hubdeck.app launcher (Contents/MacOS/hubdeck).
 *
 * Loads the bundled Python (Contents/Resources/python) into this process and runs
 * hubdeck, so macOS sees hubdeck.app - its name, icon, notifications and privacy
 * permissions - rather than a python binary.
 *
 *   hubdeck.app/Contents/MacOS/hubdeck            menu-bar app (no arguments)
 *   hubdeck.app/Contents/MacOS/hubdeck send ...   command line (works through a symlink)
 *   HUBDECK_PYTHON=1 .../hubdeck -c ...           plain Python with hubdeck's packages
 *
 * Cross-compiled on Linux with: zig cc -target aarch64-macos.11.0 (see build.py).
 */
#include <dlfcn.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifndef PYTHON_LIB
#define PYTHON_LIB "libpython3.13.dylib"
#endif

static void parent(char *path) {       /* "/a/b/c" -> "/a/b" in place */
    char *slash = strrchr(path, '/');
    if (slash && slash != path)
        *slash = '\0';
}

int main(int argc, char **argv) {
    char exe[PATH_MAX], contents[PATH_MAX], buf[PATH_MAX];
    uint32_t size = sizeof exe;
    if (_NSGetExecutablePath(exe, &size) != 0 || !realpath(exe, contents)) {
        fprintf(stderr, "hubdeck: cannot find the app bundle\n");
        return 1;
    }
    parent(contents);                   /* .../Contents/MacOS/hubdeck -> .../Contents/MacOS */
    parent(contents);                   /* -> .../Contents */

    snprintf(buf, sizeof buf, "%s/Resources/python", contents);
    setenv("PYTHONHOME", buf, 1);
    snprintf(buf, sizeof buf, "%s/Resources/app", contents);
    setenv("PYTHONPATH", buf, 1);
    setenv("PYTHONNOUSERSITE", "1", 1);
    setenv("PYTHONDONTWRITEBYTECODE", "1", 1);   /* the bundle is signed: never write into it */
    unsetenv("PYTHONSTARTUP");

    snprintf(buf, sizeof buf, "%s/Resources/python/lib/%s", contents, PYTHON_LIB);
    void *py = dlopen(buf, RTLD_NOW | RTLD_GLOBAL);
    if (!py) {
        fprintf(stderr, "hubdeck: cannot load Python: %s\n", dlerror());
        return 1;
    }
    int (*py_main)(int, char **) = (int (*)(int, char **))dlsym(py, "Py_BytesMain");
    if (!py_main) {
        fprintf(stderr, "hubdeck: Py_BytesMain missing: %s\n", dlerror());
        return 1;
    }

    const char *plain = getenv("HUBDECK_PYTHON");
    if (plain && strcmp(plain, "1") == 0) {
        unsetenv("HUBDECK_PYTHON");
        return py_main(argc, argv);     /* behave like python itself */
    }

    /* python -m hubdeck._bundle ARGS (minus the -psn_... that old macOS versions add) */
    char **args = calloc((size_t)argc + 3, sizeof *args);
    int n = 0;
    args[n++] = argv[0];
    args[n++] = "-m";
    args[n++] = "hubdeck._bundle";
    for (int i = 1; i < argc; i++)
        if (strncmp(argv[i], "-psn_", 5) != 0)
            args[n++] = argv[i];
    args[n] = NULL;
    return py_main(n, args);
}

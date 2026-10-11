/* hubdeck.exe and hubdeck-tray.exe (the same file built twice, the tray one with -DTRAY).
 *
 * Loads the bundled Python (python\python313.dll) into this process and runs hubdeck, so
 * Windows sees hubdeck - its name, icon and version, e.g. in Task Manager - rather than
 * python.exe.
 *
 *   hubdeck.exe ARGS        python -B -m hubdeck ARGS          command line
 *   hubdeck-tray.exe ARGS   python -B -m hubdeck.tray ARGS     tray app, no console window
 *
 * python\python313._pth (written by build.py) sets sys.path and makes Python ignore PYTHON*
 * environment variables, so nothing leaks in from the user's settings or out to commands
 * hubdeck runs. -B: everything is precompiled, and the install folder is never written to.
 * python\python.exe is a plain Python with hubdeck's packages, for debugging.
 *
 * Cross-compiled on Linux with: zig cc -target x86_64-windows-gnu (see build.py).
 */
#include <windows.h>
#include <shellapi.h>
#include <stdio.h>
#include <stdlib.h>
#include <wchar.h>

#ifndef PYTHON_DLL
#define PYTHON_DLL L"python313.dll"
#endif

#ifdef TRAY
#define MODULE L"hubdeck.tray"
#else
#define MODULE L"hubdeck"
#endif

#define PATH_CHARS 32768

static int fail(const wchar_t *what) {
    wchar_t msg[600];
    swprintf(msg, sizeof msg / sizeof *msg, L"hubdeck: %ls (error %lu)", what, GetLastError());
#ifdef TRAY
    MessageBoxW(NULL, msg, L"hubdeck", MB_ICONERROR);   /* no console to print to */
#else
    fwprintf(stderr, L"%ls\n", msg);
#endif
    return 1;
}

int main(void) {
    static wchar_t dir[PATH_CHARS], dll[PATH_CHARS];
    DWORD n = GetModuleFileNameW(NULL, dir, PATH_CHARS);
    wchar_t *slash = n && n < PATH_CHARS - 16 ? wcsrchr(dir, L'\\') : NULL;
    if (!slash)
        return fail(L"cannot find its own folder");
    wcscpy(slash, L"\\python");                 /* ...\hubdeck\hubdeck.exe -> ...\hubdeck\python */
    if (swprintf(dll, PATH_CHARS, L"%ls\\%ls", dir, PYTHON_DLL) < 0)
        return fail(L"path too long");

    /* Extension modules load with LOAD_LIBRARY_SEARCH_DEFAULT_DIRS, which covers python.exe's
       own folder but not ours: add python\ so they find vcruntime140_1.dll there. */
    AddDllDirectory(dir);
    HMODULE py = LoadLibraryExW(dll, NULL, LOAD_WITH_ALTERED_SEARCH_PATH);
    if (!py)
        return fail(L"cannot load python\\" PYTHON_DLL);
    int (*py_main)(int, wchar_t **) = (int (*)(int, wchar_t **))(void *)GetProcAddress(py, "Py_Main");
    if (!py_main)
        return fail(L"Py_Main missing");

    int argc;
    wchar_t **argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    wchar_t **args = argv ? calloc((size_t)argc + 4, sizeof *args) : NULL;
    if (!args)
        return fail(L"cannot read the command line");
    int i = 0;
    args[i++] = argv[0];
    args[i++] = L"-B";
    args[i++] = L"-m";
    args[i++] = MODULE;
    for (int a = 1; a < argc; a++)
        args[i++] = argv[a];
    return py_main(i, args);
}

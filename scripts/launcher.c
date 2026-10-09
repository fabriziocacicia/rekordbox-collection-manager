#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <libgen.h>
#include <sys/stat.h>

#ifndef DEFAULT_PROJECT_ROOT
#define DEFAULT_PROJECT_ROOT "/Users/fabriziocacicia/Projects/Personal/Music/rekordbox-collection-manager"
#endif

#ifndef DEFAULT_PYTHON_HOME
#define DEFAULT_PYTHON_HOME ""
#endif

#ifndef DEFAULT_DYLIB_PATH
#define DEFAULT_DYLIB_PATH ""
#endif

#ifndef PYTHON_VERSION_STR
#define PYTHON_VERSION_STR "python3.12"
#endif

#ifndef APP_MODULE
#define APP_MODULE "rekordbox_collection_manager.gui.app"
#endif

#ifndef APP_NAME
#define APP_NAME "Rekordbox Collection Manager"
#endif

typedef int (*Py_BytesMain_t)(int argc, char **argv);

static int path_exists(const char *path) {
    struct stat st;
    return stat(path, &st) == 0;
}

static void get_parent_dir(const char *path, char *out, size_t out_size) {
    char temp[PATH_MAX];
    strncpy(temp, path, sizeof(temp) - 1);
    temp[sizeof(temp) - 1] = '\0';
    char *p = dirname(temp);
    strncpy(out, p, out_size - 1);
    out[out_size - 1] = '\0';
}

int main(int argc, char *argv[]) {
    char exe_path[PATH_MAX];
    uint32_t size = sizeof(exe_path);
    if (_NSGetExecutablePath(exe_path, &size) != 0) {
        fprintf(stderr, "Failed to get executable path\n");
        return 1;
    }

    char resolved_exe[PATH_MAX];
    if (realpath(exe_path, resolved_exe) == NULL) {
        strncpy(resolved_exe, exe_path, sizeof(resolved_exe) - 1);
        resolved_exe[sizeof(resolved_exe) - 1] = '\0';
    }

    // Determine bundle directories from resolved executable:
    // resolved_exe = .../AppName.app/Contents/MacOS/AppName
    char macos_dir[PATH_MAX];
    char contents_dir[PATH_MAX];
    char app_bundle_dir[PATH_MAX];
    char parent_of_bundle[PATH_MAX];
    char candidate_project_root[PATH_MAX];

    get_parent_dir(resolved_exe, macos_dir, sizeof(macos_dir));
    get_parent_dir(macos_dir, contents_dir, sizeof(contents_dir));
    get_parent_dir(contents_dir, app_bundle_dir, sizeof(app_bundle_dir));
    get_parent_dir(app_bundle_dir, parent_of_bundle, sizeof(parent_of_bundle));
    get_parent_dir(parent_of_bundle, candidate_project_root, sizeof(candidate_project_root));

    char project_root[PATH_MAX];
    char check_venv[PATH_MAX];
    snprintf(check_venv, sizeof(check_venv), "%s/.venv", candidate_project_root);

    if (path_exists(check_venv)) {
        strncpy(project_root, candidate_project_root, sizeof(project_root) - 1);
        project_root[sizeof(project_root) - 1] = '\0';
    } else {
        strncpy(project_root, DEFAULT_PROJECT_ROOT, sizeof(project_root) - 1);
        project_root[sizeof(project_root) - 1] = '\0';
    }

    // Set working directory to project root
    if (chdir(project_root) != 0) {
        perror("chdir project_root");
    }

    // Determine python home
    char python_home[PATH_MAX];
    strncpy(python_home, DEFAULT_PYTHON_HOME, sizeof(python_home) - 1);
    python_home[sizeof(python_home) - 1] = '\0';

    // If python_home not set, try to read home from .venv/pyvenv.cfg
    if (strlen(python_home) == 0) {
        char cfg_path[PATH_MAX];
        snprintf(cfg_path, sizeof(cfg_path), "%s/.venv/pyvenv.cfg", project_root);
        FILE *f = fopen(cfg_path, "r");
        if (f) {
            char line[1024];
            while (fgets(line, sizeof(line), f)) {
                if (strncmp(line, "home = ", 7) == 0) {
                    char *val = line + 7;
                    size_t len = strlen(val);
                    while (len > 0 && (val[len - 1] == '\n' || val[len - 1] == '\r' || val[len - 1] == ' ')) {
                        val[--len] = '\0';
                    }
                    get_parent_dir(val, python_home, sizeof(python_home));
                    break;
                }
            }
            fclose(f);
        }
    }

    if (strlen(python_home) > 0) {
        setenv("PYTHONHOME", python_home, 1);
    }

    char venv_path[PATH_MAX];
    snprintf(venv_path, sizeof(venv_path), "%s/.venv", project_root);
    setenv("VIRTUAL_ENV", venv_path, 1);

    char pythonpath[PATH_MAX * 2];
    const char *orig_pythonpath = getenv("PYTHONPATH");
    if (orig_pythonpath && strlen(orig_pythonpath) > 0) {
        snprintf(pythonpath, sizeof(pythonpath), "%s/src:%s/.venv/lib/%s/site-packages:%s",
                 project_root, project_root, PYTHON_VERSION_STR, orig_pythonpath);
    } else {
        snprintf(pythonpath, sizeof(pythonpath), "%s/src:%s/.venv/lib/%s/site-packages",
                 project_root, project_root, PYTHON_VERSION_STR);
    }
    setenv("PYTHONPATH", pythonpath, 1);

    char path_env[PATH_MAX * 2];
    const char *orig_path = getenv("PATH");
    snprintf(path_env, sizeof(path_env), "%s/.venv/bin:%s", project_root, orig_path ? orig_path : "/usr/bin:/bin");
    setenv("PATH", path_env, 1);

    setenv("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES", 0);

    // Locate dylib
    char dylib_path[PATH_MAX] = "";
    char bundle_dylib[PATH_MAX];
    snprintf(bundle_dylib, sizeof(bundle_dylib), "%s/Frameworks/lib%s.dylib", contents_dir, PYTHON_VERSION_STR);

    if (path_exists(bundle_dylib)) {
        strncpy(dylib_path, bundle_dylib, sizeof(dylib_path) - 1);
    } else if (strlen(DEFAULT_DYLIB_PATH) > 0 && path_exists(DEFAULT_DYLIB_PATH)) {
        strncpy(dylib_path, DEFAULT_DYLIB_PATH, sizeof(dylib_path) - 1);
    } else if (strlen(python_home) > 0) {
        snprintf(dylib_path, sizeof(dylib_path), "%s/lib/lib%s.dylib", python_home, PYTHON_VERSION_STR);
    }

    if (strlen(dylib_path) == 0 || !path_exists(dylib_path)) {
        fprintf(stderr, "Error: Unable to locate lib%s.dylib\n", PYTHON_VERSION_STR);
        return 1;
    }

    void *handle = dlopen(dylib_path, RTLD_NOW | RTLD_GLOBAL);
    if (!handle) {
        fprintf(stderr, "dlopen failed for %s: %s\n", dylib_path, dlerror());
        return 1;
    }

    Py_BytesMain_t py_main = (Py_BytesMain_t)dlsym(handle, "Py_BytesMain");
    if (!py_main) {
        fprintf(stderr, "dlsym failed for Py_BytesMain: %s\n", dlerror());
        return 1;
    }

    // Count arguments, filtering out macOS -psn_ arguments
    int new_argc = 3;
    for (int i = 1; i < argc; i++) {
        if (strncmp(argv[i], "-psn_", 5) != 0) {
            new_argc++;
        }
    }

    char **new_argv = (char **)malloc((new_argc + 1) * sizeof(char *));
    new_argv[0] = APP_NAME;
    new_argv[1] = "-m";
    new_argv[2] = APP_MODULE;
    int idx = 3;
    for (int i = 1; i < argc; i++) {
        if (strncmp(argv[i], "-psn_", 5) != 0) {
            new_argv[idx++] = argv[i];
        }
    }
    new_argv[new_argc] = NULL;

    return py_main(new_argc, new_argv);
}

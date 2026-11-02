# SPDX-License-Identifier: GPL-2.0-only
# cmake -DBINARY_DIR=<dir> -DINSTALL_PREFIX=<prefix> -P reset_stale_cache.cmake
# Paths a project derived from an earlier install prefix stay cached after CMAKE_INSTALL_PREFIX
# changes, and the cache itself no longer shows the old prefix. A marker records the prefix the
# tree was configured for; a tree without it or for another prefix is configured afresh.
if(NOT BINARY_DIR OR NOT INSTALL_PREFIX)
    message(FATAL_ERROR "BINARY_DIR and INSTALL_PREFIX are required")
endif()
set(marker "${BINARY_DIR}/.sonicde-install-prefix")
set(recorded "")
if(EXISTS "${marker}")
    file(READ "${marker}" recorded)
endif()
if(recorded STREQUAL INSTALL_PREFIX)
    return()
endif()
if(EXISTS "${BINARY_DIR}/CMakeCache.txt")
    message(STATUS "Not configured for install prefix '${INSTALL_PREFIX}': configuring ${BINARY_DIR} afresh")
    file(REMOVE "${BINARY_DIR}/CMakeCache.txt")
    file(REMOVE_RECURSE "${BINARY_DIR}/CMakeFiles")
endif()
file(WRITE "${marker}" "${INSTALL_PREFIX}")

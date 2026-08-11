cmake_minimum_required(VERSION 3.28)
project(SonicDEBuilder LANGUAGES CXX)

# Qt6 Core probe - fails early if system Qt is not available
find_package(Qt6 REQUIRED COMPONENTS Core)
message(STATUS "Qt6 version: ${Qt6Core_VERSION}")
message(STATUS "Qt6_DIR: ${Qt6_DIR}")

# Include generated ExternalProject definitions
list(APPEND CMAKE_MODULE_PATH ${CMAKE_CURRENT_SOURCE_DIR}/cmake)
include(SonicDEProjects)

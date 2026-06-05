cmake_minimum_required(VERSION 3.16)
project(p2c_agent C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()
add_compile_options(-O2 -Wall -Wextra -Wno-unused-parameter)

include(FetchContent)

# --- yyjson (fast JSON, vendored via FetchContent) ---------------------
FetchContent_Declare(
  yyjson
  GIT_REPOSITORY https://github.com/ibireme/yyjson.git
  GIT_TAG 0.10.0
)
FetchContent_MakeAvailable(yyjson)

find_package(Threads REQUIRED)
find_package(PkgConfig REQUIRED)
pkg_check_modules(LWS REQUIRED libwebsockets)
pkg_check_modules(CURL REQUIRED libcurl)
pkg_check_modules(HIREDIS REQUIRED hiredis)

# --- sources (added incrementally as modules land) ---------------------
set(P2C_SOURCES
  src/main.c
  src/events.c
  src/config.c
  src/engineio.c
  src/parser.c
  src/ws.c
  src/taker.c
  src/account.c
  src/redis_cache.c
  # src/net_opt.c
)

# Production: build with -DP2C_USE_IMPERSONATE to link curl-impersonate for the
# Chrome TLS/H2 fingerprint (spec §4.1). We compile against stock libcurl
# headers (found above) but LINK against libcurl-impersonate.so, which is
# ABI-compatible and exports the extra curl_easy_impersonate symbol.
option(P2C_USE_IMPERSONATE "Link curl-impersonate and apply Chrome fingerprint" OFF)
set(P2C_CURL_LIBS ${CURL_LIBRARIES})
set(P2C_CURL_LIBDIRS ${CURL_LIBRARY_DIRS})
if(P2C_USE_IMPERSONATE)
  find_library(CURL_IMPERSONATE_LIB
    NAMES curl-impersonate curl-impersonate-chrome
    PATHS /usr/local/lib /usr/lib /usr/lib/x86_64-linux-gnu)
  if(NOT CURL_IMPERSONATE_LIB)
    message(FATAL_ERROR
      "P2C_USE_IMPERSONATE=ON but libcurl-impersonate was not found. "
      "Install it (e.g. extract the curl-impersonate release into /usr/local/lib).")
  endif()
  message(STATUS "curl-impersonate: ${CURL_IMPERSONATE_LIB}")
  set(P2C_CURL_LIBS ${CURL_IMPERSONATE_LIB})
  set(P2C_CURL_LIBDIRS /usr/local/lib ${CURL_LIBRARY_DIRS})
endif()

add_executable(p2c_agent ${P2C_SOURCES})
target_include_directories(p2c_agent PRIVATE include ${LWS_INCLUDE_DIRS} ${CURL_INCLUDE_DIRS} ${HIREDIS_INCLUDE_DIRS})
target_link_directories(p2c_agent PRIVATE ${LWS_LIBRARY_DIRS} ${P2C_CURL_LIBDIRS} ${HIREDIS_LIBRARY_DIRS})
target_link_libraries(p2c_agent PRIVATE yyjson Threads::Threads ${LWS_LIBRARIES} ${P2C_CURL_LIBS} ${HIREDIS_LIBRARIES})

if(P2C_USE_IMPERSONATE)
  target_compile_definitions(p2c_agent PRIVATE P2C_USE_IMPERSONATE=1)
endif()

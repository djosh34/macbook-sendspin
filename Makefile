.DEFAULT_GOAL := help
.NOTPARALLEL:

PODMAN ?= podman
SOURCE_DATE_EPOCH ?= 1748736000
BUILD_JOBS ?= 2
BUILD_NETWORK ?=
export PODMAN SOURCE_DATE_EPOCH BUILD_JOBS BUILD_NETWORK

.PHONY: help build check test clean
help:
	@printf '%s\n' \
	  'make build  Build natively; export kernel, initrd and OCI archive to out/' \
	  'make check  Run native build-container checks (never execute guest binaries)' \
	  'make clean  Remove this checkout’s out/ and temporary leftovers; retain Podman caches'

build:
	@sh scripts/build.sh build

check:
	@sh scripts/build.sh check

test: check

clean:
	@sh scripts/build.sh clean

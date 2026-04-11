.PHONY: all build restart

all: build

build:
	pyinstaller -y Tokemon.spec

restart: build
	-pkill -f Tokemon
	@sleep 1
	open dist/Tokemon.app

.PHONY: all build restart dist

all: build

build:
	pyinstaller -y Tokemon.spec

restart: build
	-pkill -f Tokemon
	@sleep 1
	open dist/Tokemon.app

dist: build
	cd dist && zip -r Tokemon.zip Tokemon.app

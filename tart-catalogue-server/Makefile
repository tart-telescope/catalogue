# (c) 2018-2023 Tim Molteno (tim@elec.ac.nz)
.PHONY: build test test-client lint

build:
	DOCKER_BUILDKIT=1 docker compose build
test:
	docker compose up --build

test-client:
	TART_CATALOGUE_URL=http://localhost:8876 cargo run --manifest-path ../tart-catalog-client-rs/Cargo.toml --release

lint:
	flake8 . --count --exit-zero --max-complexity=10 --max-line-length=127 --statistics

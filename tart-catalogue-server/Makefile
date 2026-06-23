# (c) 2018-2023 Tim Molteno (tim@elec.ac.nz)
.PHONY: test test-client lint

test:
	docker compose up -d --build

test-client:
	TART_CATALOGUE_URL=http://localhost:8876 cargo run --manifest-path ../tart-catalog-client-rs/Cargo.toml --release -- benchmark

lint:
	flake8 . --count --exit-zero --max-complexity=10 --max-line-length=127 --statistics

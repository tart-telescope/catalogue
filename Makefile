# (c) 2018-2023 Tim Molteno (tim@elec.ac.nz)
build:
	DOCKER_BUILDKIT=1 docker compose -f tart-catalogue-server/compose.yml build
test:
	docker compose -f tart-catalogue-server/compose.yml up --build

test-client:
	python3 test/test_api.py

lint:
	flake8 . --count --exit-zero --max-complexity=10 --max-line-length=127 --statistics

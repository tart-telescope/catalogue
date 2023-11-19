# (c) 2018-2023 Tim Molteno (tim@elec.ac.nz)
test:
	docker compose up --build

test-client:
	python3 app/test_api.py
	
lint:
	flake8 . --count --exit-zero --max-complexity=10 --max-line-length=127 --statistics

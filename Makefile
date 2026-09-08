.PHONY: reproduce plan clean test

reproduce:
	python3 scripts/reproduce.py

plan:
	python3 scripts/reproduce.py --plan

clean:
	python3 scripts/reproduce.py --clean

test:
	python3 -m unittest discover -s scripts -p 'test_*.py'

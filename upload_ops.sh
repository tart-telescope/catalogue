# Upload the code to a remote server that will act as an object position server.
# After install, login and run the server manually.
rsync -rv --exclude '*venv*' --exclude '__pycache__' \
    --exclude 'app_skyfield' \
    --exclude 'orbit_data' --exclude .git --exclude .gitignore \
    . tart@tart.elec.ac.nz:ops/tart_catalogue # --dry-run

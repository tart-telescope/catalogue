# Upload the code to a remote server that will act as an object position server.
# After install, login and run the server manually.
rsync -rv --exclude .git --exclude .gitignore . tart@tart.elec.ac.nz:ops/object_position_server

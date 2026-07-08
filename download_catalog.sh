# Upload the code to a remote server that will act as an object position server.
# After install, login and run the server manually.
rsync -rvz --exclude .git --exclude .gitignore \
    tart@tart.elec.ac.nz:catalog_cache .
rm -f catalog_cache.tar*
tar -cf catalog_cache.tar catalog_cache
xz -6 catalog_cache.tar

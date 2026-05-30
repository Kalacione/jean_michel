sqlite3 jeanmichel.db < db/migrations/migrate_112_web_users.sql   # si pas déjà fait
./jm.sh --create-user toi                                          # crée ton login web
./jm.sh --serve                                                    # daemon :8000
# autre terminal :
    
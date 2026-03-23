import asyncio
import asyncpg
from app.core.config import config

class PostgreSQLDatabase:
    _instance = None

    def __init__(self):
        self.connection = None

    @classmethod
    async def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
            await cls._instance.connect()
        return cls._instance

    async def connect(self):
        self.connection = await asyncpg.connect(
            host=config.database.db_host,
            port=config.database.db_port,
            user=config.database.db_user,
            password=config.database.db_password.get_secret_value(),
            database=config.database.db_name,
        )
        

    async def close(self):
        if self.connection:
            await self.connection.close()
            self.connection = None

    def get_connection_params(self):
        return {
            "host": config.database.db_host,
            "port": config.database.db_port,
            "user": config.database.db_user,
            "password": config.database.db_password.get_secret_value(),
            "database": config.database.db_name,
        }

class DatabaseFactory:
    def __init__(self):
        self._instance = None
        self.postgresql = PostgreSQLDatabase()
    
    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = config.database
        return cls._instance
    

async def main():
    db = await PostgreSQLDatabase.get_instance()
    conn_params = db.get_connection_params()
    print(conn_params)
    await db.close()

asyncio.run(main())
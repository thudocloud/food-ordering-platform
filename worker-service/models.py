from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, JSON, Enum
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
import enum
import os

Base = declarative_base()

class OrderStatus(enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    CONFIRMED = "confirmed"
    PREPARING = "preparing"
    READY = "ready"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"

class Order(Base):
    __tablename__ = 'orders'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    order_number = Column(String(50), unique=True, nullable=False, index=True)
    customer_name = Column(String(100), nullable=False)
    customer_email = Column(String(100), nullable=False)
    customer_phone = Column(String(20))
    delivery_address = Column(String(255))
    
    items = Column(JSON, nullable=False)
    
    subtotal = Column(Float, nullable=False)
    tax = Column(Float, nullable=False)
    total = Column(Float, nullable=False)
    
    status = Column(Enum(OrderStatus), default=OrderStatus.PENDING, nullable=False, index=True)
    notes = Column(String(500))
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

def get_db_engine():
    database_url = os.getenv('DATABASE_URL', 'postgresql://admin:admin123@localhost:5432/food_ordering')
    return create_engine(database_url, pool_pre_ping=True, pool_size=5, max_overflow=10)

def get_db_session():
    engine = get_db_engine()
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()
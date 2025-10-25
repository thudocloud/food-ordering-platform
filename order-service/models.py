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
    
    # Order details stored as JSON
    items = Column(JSON, nullable=False)
    
    # Pricing information
    subtotal = Column(Float, nullable=False)
    tax = Column(Float, nullable=False)
    total = Column(Float, nullable=False)
    
    # Order status and tracking
    status = Column(Enum(OrderStatus), default=OrderStatus.PENDING, nullable=False, index=True)
    notes = Column(String(500))
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    def to_dict(self):
        return {
            'id': self.id,
            'order_number': self.order_number,
            'customer_name': self.customer_name,
            'customer_email': self.customer_email,
            'customer_phone': self.customer_phone,
            'delivery_address': self.delivery_address,
            'items': self.items,
            'subtotal': self.subtotal,
            'tax': self.tax,
            'total': self.total,
            'status': self.status.value,
            'notes': self.notes,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }

# Database connection utility
def get_db_engine():
    database_url = os.getenv('DATABASE_URL', 'postgresql://admin:admin123@localhost:5432/food_ordering')
    return create_engine(database_url, pool_pre_ping=True, pool_size=10, max_overflow=20)

def get_db_session():
    engine = get_db_engine()
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()

def init_db():
    """Initialize database tables"""
    engine = get_db_engine()
    try:
        Base.metadata.create_all(engine, checkfirst=True)
        print("Database tables created successfully")
    except Exception as e:
        print(f"Database initialization: {e}")
        # Tables might already exist, which is fine
        pass
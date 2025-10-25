import pika
import json
import os
import time
from datetime import datetime
from models import Order, OrderStatus, get_db_session
from sqlalchemy.exc import SQLAlchemyError

# Configuration
RABBITMQ_HOST = os.getenv('RABBITMQ_HOST', 'localhost')
RABBITMQ_USER = os.getenv('RABBITMQ_USER', 'admin')
RABBITMQ_PASS = os.getenv('RABBITMQ_PASS', 'admin123')

def get_rabbitmq_connection():
    """Create RabbitMQ connection with retry logic"""
    max_retries = 5
    retry_delay = 5
    
    for attempt in range(max_retries):
        try:
            credentials = pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASS)
            parameters = pika.ConnectionParameters(
                host=RABBITMQ_HOST,
                credentials=credentials,
                heartbeat=600,
                blocked_connection_timeout=300
            )
            connection = pika.BlockingConnection(parameters)
            print(f"Connected to RabbitMQ at {RABBITMQ_HOST}")
            return connection
        except pika.exceptions.AMQPConnectionError as e:
            if attempt < max_retries - 1:
                print(f"Connection attempt {attempt + 1} failed. Retrying in {retry_delay}s...")
                time.sleep(retry_delay)
            else:
                print(f"Failed to connect to RabbitMQ after {max_retries} attempts")
                raise

def send_confirmation_email(customer_email, order_number, total):
    """
    Simulate sending confirmation email
    In production, integrate with email service like SendGrid, AWS SES, etc.
    """
    print(f"📧 Sending confirmation email to {customer_email}")
    print(f"   Order Number: {order_number}")
    print(f"   Total: ${total:.2f}")
    print(f"   Email sent successfully!")
    
    # Simulate email processing time
    time.sleep(0.5)
    return True

def process_order(order_id, order_number, customer_email, total):
    """
    Process the order:
    1. Update order status to PROCESSING
    2. Send confirmation email
    3. Update status to CONFIRMED
    """
    session = get_db_session()
    
    try:
        # Get order from database
        order = session.query(Order).filter_by(id=order_id).first()
        
        if not order:
            print(f"❌ Order {order_number} not found in database")
            session.close()
            return False
        
        print(f"\n{'='*60}")
        print(f"🔄 Processing Order: {order_number}")
        print(f"   Customer: {order.customer_name}")
        print(f"   Email: {customer_email}")
        print(f"   Total: ${total:.2f}")
        print(f"   Items: {len(order.items)}")
        
        # Update status to PROCESSING
        order.status = OrderStatus.PROCESSING
        order.updated_at = datetime.utcnow()
        session.commit()
        print(f"✓ Status updated to PROCESSING")
        
        # Send confirmation email
        email_sent = send_confirmation_email(customer_email, order_number, total)
        
        if email_sent:
            # Update status to CONFIRMED
            order.status = OrderStatus.CONFIRMED
            order.updated_at = datetime.utcnow()
            session.commit()
            print(f"✓ Status updated to CONFIRMED")
            print(f"✅ Order {order_number} processed successfully")
            print(f"{'='*60}\n")
            
            session.close()
            return True
        else:
            print(f"⚠️  Email sending failed for order {order_number}")
            session.close()
            return False
            
    except SQLAlchemyError as e:
        print(f"❌ Database error processing order {order_number}: {e}")
        session.rollback()
        session.close()
        return False
    except Exception as e:
        print(f"❌ Error processing order {order_number}: {e}")
        session.close()
        return False

def callback(ch, method, properties, body):
    """Callback function for processing messages from queue"""
    try:
        # Parse message
        message = json.loads(body)
        order_id = message.get('order_id')
        order_number = message.get('order_number')
        customer_email = message.get('customer_email')
        total = message.get('total')
        
        # Process the order
        success = process_order(order_id, order_number, customer_email, total)
        
        if success:
            # Acknowledge the message
            ch.basic_ack(delivery_tag=method.delivery_tag)
        else:
            # Reject and requeue the message for retry
            print(f"⚠️  Requeuing order {order_number} for retry")
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
            
    except json.JSONDecodeError as e:
        print(f"❌ Invalid JSON message: {e}")
        # Acknowledge bad message to remove it from queue
        ch.basic_ack(delivery_tag=method.delivery_tag)
    except Exception as e:
        print(f"❌ Error in callback: {e}")
        # Requeue message for retry
        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)

def main():
    """Main worker function"""
    print("🚀 Starting Order Worker Service")
    print(f"   RabbitMQ Host: {RABBITMQ_HOST}")
    print(f"   Database: Connected")
    print(f"   Waiting for orders...\n")
    
    # Connect to RabbitMQ
    connection = get_rabbitmq_connection()
    channel = connection.channel()
    
    # Declare queue (idempotent)
    channel.queue_declare(queue='orders', durable=True)
    
    # Set prefetch count to process one message at a time
    channel.basic_qos(prefetch_count=1)
    
    # Start consuming messages
    channel.basic_consume(
        queue='orders',
        on_message_callback=callback,
        auto_ack=False  # Manual acknowledgment
    )
    
    print("✓ Worker is ready to process orders")
    print("✓ Press CTRL+C to stop\n")
    
    try:
        channel.start_consuming()
    except KeyboardInterrupt:
        print("\n\n🛑 Shutting down worker...")
        channel.stop_consuming()
        connection.close()
        print("✓ Worker stopped gracefully")

if __name__ == '__main__':
    main()
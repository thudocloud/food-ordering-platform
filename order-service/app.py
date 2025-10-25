from flask import Flask, request, jsonify
from flask_cors import CORS
import pika
import json
import os
import requests
import redis
from datetime import datetime
import uuid
from models import Order, OrderStatus, init_db, get_db_session
from sqlalchemy.exc import SQLAlchemyError

app = Flask(__name__)
CORS(app)

# Configuration
PRICING_SERVICE_URL = os.getenv('PRICING_SERVICE_URL', 'http://localhost:5001')
RABBITMQ_HOST = os.getenv('RABBITMQ_HOST', 'localhost')
RABBITMQ_USER = os.getenv('RABBITMQ_USER', 'admin')
RABBITMQ_PASS = os.getenv('RABBITMQ_PASS', 'admin123')
REDIS_HOST = os.getenv('REDIS_HOST', 'localhost')
REDIS_PORT = int(os.getenv('REDIS_PORT', 6379))

# Redis connection for caching
redis_client = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    decode_responses=True
)

# Initialize database on startup
init_db()

def get_rabbitmq_connection():
    """Create RabbitMQ connection"""
    credentials = pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASS)
    parameters = pika.ConnectionParameters(
        host=RABBITMQ_HOST,
        credentials=credentials,
        heartbeat=600,
        blocked_connection_timeout=300
    )
    return pika.BlockingConnection(parameters)

def publish_to_queue(order_data):
    """Publish order to RabbitMQ queue"""
    try:
        connection = get_rabbitmq_connection()
        channel = connection.channel()
        
        # Declare queue (idempotent operation)
        channel.queue_declare(queue='orders', durable=True)
        
        # Publish message
        channel.basic_publish(
            exchange='',
            routing_key='orders',
            body=json.dumps(order_data),
            properties=pika.BasicProperties(
                delivery_mode=2,  # Make message persistent
                content_type='application/json'
            )
        )
        
        connection.close()
        return True
    except Exception as e:
        print(f"Error publishing to queue: {e}")
        return False

def generate_order_number():
    """Generate unique order number"""
    timestamp = datetime.utcnow().strftime('%Y%m%d%H%M%S')
    unique_id = str(uuid.uuid4())[:8].upper()
    return f"ORD-{timestamp}-{unique_id}"

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    try:
        # Check database
        session = get_db_session()
        session.execute('SELECT 1')
        session.close()
        
        # Check Redis
        redis_client.ping()
        
        # Check RabbitMQ
        connection = get_rabbitmq_connection()
        connection.close()
        
        return jsonify({
            "status": "healthy",
            "service": "order-service",
            "database": "connected",
            "redis": "connected",
            "rabbitmq": "connected"
        }), 200
    except Exception as e:
        return jsonify({
            "status": "unhealthy",
            "error": str(e)
        }), 503

@app.route('/menu', methods=['GET'])
def get_menu():
    """Proxy request to pricing service for menu"""
    try:
        response = requests.get(f"{PRICING_SERVICE_URL}/menu", timeout=5)
        return jsonify(response.json()), response.status_code
    except requests.exceptions.RequestException as e:
        return jsonify({"error": "Unable to fetch menu", "details": str(e)}), 503

@app.route('/orders', methods=['POST'])
def create_order():
    """
    Create new order - Fast API endpoint (<200ms)
    Expected JSON:
    {
        "customer_name": "John Doe",
        "customer_email": "john@example.com",
        "customer_phone": "555-1234",
        "delivery_address": "123 Main St",
        "items": [
            {"item_id": "burger", "quantity": 2},
            {"item_id": "fries", "quantity": 1}
        ],
        "notes": "Extra ketchup please"
    }
    """
    try:
        data = request.get_json()
        
        # Validate required fields
        required_fields = ['customer_name', 'customer_email', 'items']
        for field in required_fields:
            if field not in data or not data[field]:
                return jsonify({"error": f"Missing required field: {field}"}), 400
        
        # Validate items
        if not isinstance(data['items'], list) or len(data['items']) == 0:
            return jsonify({"error": "Items must be a non-empty list"}), 400
        
        # Calculate pricing from pricing service
        pricing_response = requests.post(
            f"{PRICING_SERVICE_URL}/calculate",
            json={"items": data['items']},
            timeout=3
        )
        
        if pricing_response.status_code != 200:
            return jsonify({
                "error": "Unable to calculate pricing",
                "details": pricing_response.json()
            }), pricing_response.status_code
        
        pricing_data = pricing_response.json()
        
        # Generate order number
        order_number = generate_order_number()
        
        # Create order in database
        session = get_db_session()
        try:
            order = Order(
                order_number=order_number,
                customer_name=data['customer_name'],
                customer_email=data['customer_email'],
                customer_phone=data.get('customer_phone'),
                delivery_address=data.get('delivery_address'),
                items=pricing_data['items'],
                subtotal=pricing_data['subtotal'],
                tax=pricing_data['tax'],
                total=pricing_data['total'],
                status=OrderStatus.PENDING,
                notes=data.get('notes')
            )
            
            session.add(order)
            session.commit()
            
            order_id = order.id
            order_dict = order.to_dict()
            
            session.close()
            
            # Publish to RabbitMQ for async processing
            queue_message = {
                "order_id": order_id,
                "order_number": order_number,
                "customer_email": data['customer_email'],
                "total": pricing_data['total']
            }
            
            publish_success = publish_to_queue(queue_message)
            
            if not publish_success:
                print(f"Warning: Order {order_number} created but not queued")
            
            # Cache order for quick retrieval
            cache_key = f"order:{order_number}"
            redis_client.setex(cache_key, 300, json.dumps(order_dict))
            
            return jsonify({
                "message": "Order created successfully",
                "order": order_dict,
                "queued": publish_success
            }), 201
            
        except SQLAlchemyError as e:
            session.rollback()
            session.close()
            return jsonify({"error": "Database error", "details": str(e)}), 500
            
    except requests.exceptions.Timeout:
        return jsonify({"error": "Pricing service timeout"}), 504
    except requests.exceptions.RequestException as e:
        return jsonify({"error": "Pricing service unavailable", "details": str(e)}), 503
    except Exception as e:
        return jsonify({"error": "Internal server error", "details": str(e)}), 500

@app.route('/orders/<order_number>', methods=['GET'])
def get_order(order_number):
    """Get order by order number"""
    try:
        # Try cache first
        cache_key = f"order:{order_number}"
        cached_order = redis_client.get(cache_key)
        
        if cached_order:
            return jsonify({
                "order": json.loads(cached_order),
                "cached": True
            }), 200
        
        # If not cached, query database
        session = get_db_session()
        order = session.query(Order).filter_by(order_number=order_number).first()
        session.close()
        
        if not order:
            return jsonify({"error": "Order not found"}), 404
        
        order_dict = order.to_dict()
        
        # Cache for future requests
        redis_client.setex(cache_key, 300, json.dumps(order_dict))
        
        return jsonify({
            "order": order_dict,
            "cached": False
        }), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/orders', methods=['GET'])
def list_orders():
    """List all orders with optional filtering"""
    try:
        # Query parameters
        status = request.args.get('status')
        limit = int(request.args.get('limit', 50))
        offset = int(request.args.get('offset', 0))
        
        session = get_db_session()
        query = session.query(Order)
        
        # Filter by status if provided
        if status:
            try:
                status_enum = OrderStatus[status.upper()]
                query = query.filter_by(status=status_enum)
            except KeyError:
                session.close()
                return jsonify({"error": f"Invalid status: {status}"}), 400
        
        # Order by most recent first
        query = query.order_by(Order.created_at.desc())
        
        # Pagination
        total_count = query.count()
        orders = query.limit(limit).offset(offset).all()
        
        session.close()
        
        return jsonify({
            "orders": [order.to_dict() for order in orders],
            "total": total_count,
            "limit": limit,
            "offset": offset
        }), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/orders/<order_number>/status', methods=['PATCH'])
def update_order_status(order_number):
    """
    Update order status
    Expected JSON:
    {
        "status": "confirmed"
    }
    """
    try:
        data = request.get_json()
        
        if 'status' not in data:
            return jsonify({"error": "Status field required"}), 400
        
        # Validate status
        try:
            new_status = OrderStatus[data['status'].upper()]
        except KeyError:
            return jsonify({"error": f"Invalid status: {data['status']}"}), 400
        
        session = get_db_session()
        order = session.query(Order).filter_by(order_number=order_number).first()
        
        if not order:
            session.close()
            return jsonify({"error": "Order not found"}), 404
        
        order.status = new_status
        order.updated_at = datetime.utcnow()
        
        session.commit()
        order_dict = order.to_dict()
        session.close()
        
        # Invalidate cache
        cache_key = f"order:{order_number}"
        redis_client.delete(cache_key)
        
        return jsonify({
            "message": "Order status updated",
            "order": order_dict
        }), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/orders/<order_number>', methods=['DELETE'])
def cancel_order(order_number):
    """Cancel an order"""
    try:
        session = get_db_session()
        order = session.query(Order).filter_by(order_number=order_number).first()
        
        if not order:
            session.close()
            return jsonify({"error": "Order not found"}), 404
        
        # Check if order can be cancelled
        if order.status in [OrderStatus.DELIVERED, OrderStatus.CANCELLED]:
            session.close()
            return jsonify({
                "error": f"Cannot cancel order with status: {order.status.value}"
            }), 400
        
        order.status = OrderStatus.CANCELLED
        order.updated_at = datetime.utcnow()
        
        session.commit()
        session.close()
        
        # Invalidate cache
        cache_key = f"order:{order_number}"
        redis_client.delete(cache_key)
        
        return jsonify({"message": "Order cancelled successfully"}), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/stats', methods=['GET'])
def get_stats():
    """Get order statistics"""
    try:
        session = get_db_session()
        
        # Total orders
        total_orders = session.query(Order).count()
        
        # Orders by status
        status_counts = {}
        for status in OrderStatus:
            count = session.query(Order).filter_by(status=status).count()
            status_counts[status.value] = count
        
        # Total revenue
        from sqlalchemy import func
        total_revenue = session.query(func.sum(Order.total)).scalar() or 0.0
        
        session.close()
        
        return jsonify({
            "total_orders": total_orders,
            "status_breakdown": status_counts,
            "total_revenue": round(total_revenue, 2)
        }), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
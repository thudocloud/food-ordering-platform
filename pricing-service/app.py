from flask import Flask, request, jsonify
import redis
import json
import os

app = Flask(__name__)

# Redis connection
redis_client = redis.Redis(
    host=os.getenv('REDIS_HOST', 'localhost'),
    port=int(os.getenv('REDIS_PORT', 6379)),
    decode_responses=True
)

# Sample menu with prices (in production, this would come from a database)
MENU_ITEMS = {
    "burger": {"name": "Classic Burger", "price": 8.99, "category": "main"},
    "pizza": {"name": "Margherita Pizza", "price": 12.99, "category": "main"},
    "pasta": {"name": "Spaghetti Carbonara", "price": 10.99, "category": "main"},
    "salad": {"name": "Caesar Salad", "price": 6.99, "category": "appetizer"},
    "fries": {"name": "French Fries", "price": 3.99, "category": "side"},
    "soda": {"name": "Soft Drink", "price": 2.49, "category": "beverage"},
    "water": {"name": "Bottled Water", "price": 1.99, "category": "beverage"},
    "cake": {"name": "Chocolate Cake", "price": 5.99, "category": "dessert"}
}

# Initialize Redis cache with menu items
def init_cache():
    """Cache menu items in Redis on startup"""
    try:
        for item_id, item_data in MENU_ITEMS.items():
            cache_key = f"menu:{item_id}"
            redis_client.setex(cache_key, 3600, json.dumps(item_data))  # Cache for 1 hour
        print("Menu items cached successfully")
    except Exception as e:
        print(f"Error caching menu items: {e}")

init_cache()

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    try:
        redis_client.ping()
        return jsonify({"status": "healthy", "service": "pricing-service"}), 200
    except Exception as e:
        return jsonify({"status": "unhealthy", "error": str(e)}), 503

@app.route('/menu', methods=['GET'])
def get_menu():
    """Get all menu items"""
    try:
        menu = {}
        for item_id in MENU_ITEMS.keys():
            cache_key = f"menu:{item_id}"
            cached_item = redis_client.get(cache_key)
            if cached_item:
                menu[item_id] = json.loads(cached_item)
            else:
                # If not in cache, get from MENU_ITEMS and cache it
                item = MENU_ITEMS[item_id]
                redis_client.setex(cache_key, 3600, json.dumps(item))
                menu[item_id] = item
        
        return jsonify({"menu": menu}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/menu/<item_id>', methods=['GET'])
def get_menu_item(item_id):
    """Get specific menu item by ID"""
    try:
        cache_key = f"menu:{item_id}"
        cached_item = redis_client.get(cache_key)
        
        if cached_item:
            return jsonify({"item": json.loads(cached_item), "cached": True}), 200
        
        # If not in cache, check MENU_ITEMS
        if item_id in MENU_ITEMS:
            item = MENU_ITEMS[item_id]
            redis_client.setex(cache_key, 3600, json.dumps(item))
            return jsonify({"item": item, "cached": False}), 200
        
        return jsonify({"error": "Item not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/calculate', methods=['POST'])
def calculate_price():
    """
    Calculate total price for an order
    Expected JSON:
    {
        "items": [
            {"item_id": "burger", "quantity": 2},
            {"item_id": "fries", "quantity": 1}
        ]
    }
    """
    try:
        data = request.get_json()
        
        if not data or 'items' not in data:
            return jsonify({"error": "Invalid request. 'items' field required"}), 400
        
        items = data['items']
        total = 0.0
        item_details = []
        
        for item in items:
            item_id = item.get('item_id')
            quantity = item.get('quantity', 1)
            
            if not item_id:
                return jsonify({"error": "item_id required for each item"}), 400
            
            # Get item from cache
            cache_key = f"menu:{item_id}"
            cached_item = redis_client.get(cache_key)
            
            if cached_item:
                menu_item = json.loads(cached_item)
            elif item_id in MENU_ITEMS:
                menu_item = MENU_ITEMS[item_id]
                redis_client.setex(cache_key, 3600, json.dumps(menu_item))
            else:
                return jsonify({"error": f"Item '{item_id}' not found"}), 404
            
            item_total = menu_item['price'] * quantity
            total += item_total
            
            item_details.append({
                "item_id": item_id,
                "name": menu_item['name'],
                "quantity": quantity,
                "unit_price": menu_item['price'],
                "subtotal": round(item_total, 2)
            })
        
        # Calculate tax (8% sales tax)
        tax = total * 0.08
        grand_total = total + tax
        
        response = {
            "items": item_details,
            "subtotal": round(total, 2),
            "tax": round(tax, 2),
            "total": round(grand_total, 2)
        }
        
        return jsonify(response), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=True)
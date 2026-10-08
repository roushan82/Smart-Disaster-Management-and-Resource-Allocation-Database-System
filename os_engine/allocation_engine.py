from config.database import get_db_connection

PRIORITY_VALUES = {
    "CRITICAL": 4,
    "HIGH": 3,
    "MEDIUM": 2,
    "LOW": 1
}

def allocate_request(request_id):
    connection = get_db_connection()
    cursor = connection.cursor()
    try:
        # Start transaction
        connection.start_transaction()
        # Get request details
        cursor.execute("""
            SELECT
                request_id,
                resource_type_id,
                requested_quantity,
                priority,
                status
            FROM emergency_requests
            WHERE request_id = %s
            FOR UPDATE
        """, (request_id,))
        request_data = cursor.fetchone()
        if not request_data:
            raise Exception("Request not found.")
        req_id, resource_type_id, requested_quantity, priority, status = request_data
        # Only pending requests can be allocated
        if status != "PENDING":
            raise Exception(
                f"Request {request_id} is not PENDING."
            )
        # Find an available resource
        cursor.execute("""
            SELECT
                resource_id,
                available_quantity
            FROM resources
            WHERE type_id = %s
              AND status = 'AVAILABLE'
              AND available_quantity > 0
            ORDER BY available_quantity DESC
            LIMIT 1
            FOR UPDATE
        """, (resource_type_id,))
        resource_data = cursor.fetchone()
        if not resource_data:
            connection.rollback()
            return False, "No available resource found."
        resource_id, available_quantity = resource_data
        # Check whether enough quantity is available
        if available_quantity < requested_quantity:
            connection.rollback()
            return False, (
                f"Insufficient quantity. "
                f"Required: {requested_quantity}, "
                f"Available: {available_quantity}"
            )
        # Reduce available quantity
        cursor.execute("""
            UPDATE resources
            SET available_quantity = available_quantity - %s
            WHERE resource_id = %s
        """, (requested_quantity, resource_id))
        # Create allocation record
        cursor.execute("""
            INSERT INTO allocations
            (request_id, resource_id, allocated_quantity, status)
            VALUES (%s, %s, %s, 'ALLOCATED')
        """, (
            request_id,
            resource_id,
            requested_quantity
        ))
        # Update request status
        cursor.execute("""
            UPDATE emergency_requests
            SET status = 'ALLOCATED'
            WHERE request_id = %s
        """, (request_id,))
        # Commit transaction
        connection.commit()
        return True, (
            f"Request {request_id} allocated successfully."
        )
    except Exception as e:
        connection.rollback()
        return False, str(e)
    finally:
        cursor.close()
        connection.close()

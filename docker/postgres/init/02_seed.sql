SELECT setseed(0.42);

INSERT INTO customers (name, email, country, created_at)
SELECT
    'Customer ' || g,
    'customer' || g || '@example.com',
    (ARRAY['India', 'USA', 'UK', 'Germany', 'Canada', 'UAE'])[1 + floor(random() * 6)::int],
    now() - (random() * interval '730 days')
FROM generate_series(1, 200) AS g;

INSERT INTO products (name, category, price)
SELECT
    'Product ' || g,
    (ARRAY['Electronics', 'Books', 'Clothing', 'Home', 'Sports'])[1 + floor(random() * 5)::int],
    round((5 + random() * 495)::numeric, 2)
FROM generate_series(1, 50) AS g;

INSERT INTO orders (customer_id, status, ordered_at)
SELECT
    1 + floor(random() * 200)::int,
    (ARRAY['pending', 'paid', 'shipped', 'cancelled'])[1 + floor(random() * 4)::int],
    now() - (random() * interval '365 days')
FROM generate_series(1, 2000);

INSERT INTO order_items (order_id, product_id, quantity, unit_price)
SELECT item.order_id, p.id, item.quantity, p.price
FROM (
    SELECT
        o.id AS order_id,
        1 + floor(random() * 50)::int AS product_id,
        1 + floor(random() * 4)::int AS quantity
    FROM orders o
    CROSS JOIN LATERAL generate_series(1, 1 + o.id % 3)
) AS item
JOIN products p ON p.id = item.product_id;

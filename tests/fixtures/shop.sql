-- Small, documented shop database used by integration tests.
CREATE TABLE customers (
    id          SERIAL PRIMARY KEY,
    name        TEXT        NOT NULL,
    email       TEXT        NOT NULL UNIQUE,
    country     TEXT        NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE products (
    id        SERIAL PRIMARY KEY,
    name      TEXT          NOT NULL,
    category  TEXT          NOT NULL,
    price     NUMERIC(10,2) NOT NULL CHECK (price >= 0)
);

CREATE TABLE orders (
    id           SERIAL PRIMARY KEY,
    customer_id  INT         NOT NULL REFERENCES customers(id),
    status       TEXT        NOT NULL CHECK (status IN ('pending', 'paid', 'shipped', 'cancelled')),
    ordered_at   TIMESTAMPTZ NOT NULL
);

CREATE TABLE order_items (
    id          SERIAL PRIMARY KEY,
    order_id    INT           NOT NULL REFERENCES orders(id),
    product_id  INT           NOT NULL REFERENCES products(id),
    quantity    INT           NOT NULL CHECK (quantity > 0),
    unit_price  NUMERIC(10,2) NOT NULL
);

CREATE INDEX ON orders (customer_id);
CREATE INDEX ON orders (ordered_at);
CREATE INDEX ON order_items (order_id);
CREATE INDEX ON order_items (product_id);

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

COMMENT ON TABLE customers IS 'People with a shop account';
COMMENT ON COLUMN customers.country IS 'Country the customer lives in';
COMMENT ON COLUMN customers.created_at IS 'When the account was created';

COMMENT ON TABLE products IS 'Items for sale in the catalogue';
COMMENT ON COLUMN products.price IS 'Current list price in USD';

COMMENT ON TABLE orders IS 'One row per checkout by a customer';
COMMENT ON COLUMN orders.status IS 'Lifecycle state; only paid and shipped orders count as revenue';
COMMENT ON COLUMN orders.ordered_at IS 'When the customer placed the order';

COMMENT ON TABLE order_items IS 'Products and quantities within an order';
COMMENT ON COLUMN order_items.unit_price IS 'Price in USD paid per unit at order time';

ANALYZE;

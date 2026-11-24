-- The shop sample database for MySQL, used by the MySQL integration tests.
SET SESSION cte_max_recursion_depth = 5000;

CREATE TABLE customers (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    name        VARCHAR(100) NOT NULL,
    email       VARCHAR(150) NOT NULL UNIQUE,
    country     VARCHAR(2)   NOT NULL COMMENT 'ISO country code',
    created_at  DATETIME     NOT NULL
) COMMENT = 'People who have bought something';

CREATE TABLE products (
    id        INT AUTO_INCREMENT PRIMARY KEY,
    name      VARCHAR(100)  NOT NULL,
    category  ENUM('books', 'toys', 'garden', 'kitchen') NOT NULL,
    price     DECIMAL(10,2) NOT NULL
);

CREATE TABLE orders (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    customer_id  INT NOT NULL,
    status       ENUM('pending', 'paid', 'shipped', 'cancelled') NOT NULL,
    ordered_at   DATETIME NOT NULL,
    FOREIGN KEY (customer_id) REFERENCES customers (id)
);

CREATE TABLE order_items (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    order_id    INT NOT NULL,
    product_id  INT NOT NULL,
    quantity    INT NOT NULL,
    unit_price  DECIMAL(10,2) NOT NULL,
    FOREIGN KEY (order_id) REFERENCES orders (id),
    FOREIGN KEY (product_id) REFERENCES products (id)
);

INSERT INTO customers (name, email, country, created_at)
WITH RECURSIVE n (i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 100)
SELECT CONCAT('Customer ', i), CONCAT('customer', i, '@example.com'),
       ELT(1 + i MOD 4, 'US', 'DE', 'IN', 'BR'),
       TIMESTAMP('2024-01-01') + INTERVAL i DAY
FROM n;

INSERT INTO products (name, category, price)
WITH RECURSIVE n (i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 40)
SELECT CONCAT('Product ', i), ELT(1 + i MOD 4, 'books', 'toys', 'garden', 'kitchen'),
       5 + i * 1.5
FROM n;

INSERT INTO orders (customer_id, status, ordered_at)
WITH RECURSIVE n (i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 2000)
SELECT 1 + i MOD 100, ELT(1 + i MOD 4, 'pending', 'paid', 'shipped', 'cancelled'),
       TIMESTAMP('2025-01-01') + INTERVAL i HOUR
FROM n;

INSERT INTO order_items (order_id, product_id, quantity, unit_price)
WITH RECURSIVE n (i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 4000)
SELECT 1 + i MOD 2000, 1 + i MOD 40, 1 + i MOD 3, 5 + (i MOD 40) * 1.5
FROM n;

-- No foreign key is declared here: the link to customers has to be inferred.
CREATE TABLE visits (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    customer_id  INT NOT NULL,
    visited_at   DATETIME NOT NULL
);

INSERT INTO visits (customer_id, visited_at)
WITH RECURSIVE n (i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 300)
SELECT 1 + i MOD 100, TIMESTAMP('2025-03-01') + INTERVAL i HOUR
FROM n;

CREATE VIEW customer_orders AS
SELECT c.id AS customer_id, c.name, COUNT(o.id) AS orders
FROM customers c LEFT JOIN orders o ON o.customer_id = c.id
GROUP BY c.id, c.name;

ANALYZE TABLE customers, products, orders, order_items, visits;

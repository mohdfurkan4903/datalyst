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

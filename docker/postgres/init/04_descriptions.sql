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

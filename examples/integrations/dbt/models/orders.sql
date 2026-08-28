select *
from (
    values
        (1001, 'C001', 42.50, '2026-08-01', 'paid'),
        (1002, 'C002', 18.99, '2026-08-02', 'paid'),
        (1003, 'C003', 125.00, '2026-08-03', 'shipped'),
        (1004, 'C004', 67.25, '2026-02-30', 'paid'), -- intentionally impossible date
        (1005, 'C005', 31.10, '2026-08-05', 'shipped'),
        (1006, 'C006', 88.40, '2026-08-06', 'paid')
) as orders(order_id, customer_id, amount, event_date, status)

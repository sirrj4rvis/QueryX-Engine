-- QueryX sample queries.
-- Paste these into the interactive shell:  python -m queryx demo
-- (Shell meta-commands, NOT SQL: .tables  .schema <table>  .indexes  .recommend  .apply  .quit)

-- ============================ setup (multi-row INSERT) ============================
CREATE TABLE departments (id INT, name TEXT, budget INT);
INSERT INTO departments VALUES
  (1, 'Engineering', 500000),
  (2, 'Sales', 300000),
  (3, 'Marketing', 200000);

CREATE TABLE employees (id INT, name TEXT, dept_id INT, salary INT, age INT);
INSERT INTO employees VALUES
  (1, 'alice', 1, 95000, 30),
  (2, 'bob',   1, 80000, 25),
  (3, 'carol', 2, 70000, 40),
  (4, 'dave',  2, 60000, 35),
  (5, 'erin',  3, 75000, 28),
  (6, 'frank', 1, 120000, 45),
  (7, 'grace', 2, 90000, 38),
  (8, 'heidi', 3, 50000, 23);

-- ============================ SELECT / projection / DISTINCT ============================
SELECT * FROM employees;
SELECT name, salary FROM employees;
SELECT DISTINCT dept_id FROM employees;

-- ============================ WHERE  (= != <> < > <= >=, AND OR NOT) ============================
SELECT name, salary FROM employees WHERE salary >= 80000;
SELECT name FROM employees WHERE dept_id = 1 AND age < 40;
SELECT name FROM employees WHERE dept_id = 3 OR salary > 100000;
SELECT name FROM employees WHERE NOT dept_id = 1;
SELECT name FROM employees WHERE age <> 30;
SELECT name FROM employees WHERE (dept_id = 1 OR dept_id = 2) AND age >= 35;
SELECT name, salary, age FROM employees WHERE salary > age;   -- column vs column

-- ============================ ORDER BY / LIMIT ============================
SELECT name, salary FROM employees ORDER BY salary DESC;
SELECT name, age FROM employees ORDER BY age ASC LIMIT 3;
SELECT name, dept_id, salary FROM employees ORDER BY dept_id ASC, salary DESC;

-- ============================ scalar aggregates ============================
SELECT COUNT(*) FROM employees;
SELECT MIN(salary), MAX(salary), AVG(salary) FROM employees;
SELECT SUM(salary) FROM employees WHERE dept_id = 1;

-- ============================ GROUP BY / HAVING ============================
SELECT dept_id, COUNT(*) FROM employees GROUP BY dept_id;
SELECT dept_id, AVG(salary), MAX(salary) FROM employees GROUP BY dept_id;
SELECT dept_id, COUNT(*) FROM employees GROUP BY dept_id HAVING COUNT(*) >= 3;
SELECT dept_id, SUM(salary) FROM employees GROUP BY dept_id ORDER BY dept_id;

-- ============================ two-table INNER JOIN (qualified names) ============================
SELECT e.name, d.name FROM employees e JOIN departments d ON e.dept_id = d.id;
SELECT e.name, e.salary, d.name FROM employees e JOIN departments d ON e.dept_id = d.id
  WHERE e.salary > 80000 ORDER BY e.salary DESC;
CREATE INDEX idx_dept ON departments (id);   -- enables an index-nested-loop join
EXPLAIN SELECT e.name, d.name FROM employees e JOIN departments d ON e.dept_id = d.id;

-- ============================ indexes + EXPLAIN ============================
CREATE INDEX idx_emp_id ON employees (id);
EXPLAIN SELECT name FROM employees WHERE id = 4;   -- tiny table: SeqScan is correctly cheaper

-- ============================ UPDATE / DELETE ============================
UPDATE employees SET salary = 130000 WHERE name = 'frank';
SELECT name, salary FROM employees WHERE name = 'frank';
UPDATE employees SET dept_id = 2, age = 41 WHERE id = 7;
DELETE FROM employees WHERE salary < 55000;
SELECT name, salary FROM employees ORDER BY salary;

-- ============================ cleanup ============================
DROP INDEX idx_emp_id;
DROP INDEX idx_dept;
DROP TABLE employees;
DROP TABLE departments;

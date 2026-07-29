"""
City distance matrices for the Travelling Salesman Problem example.

Each matrix is a square matrix where matrix[i][j]
represents the distance from city i to city j.
"""

CITY_4 = [
    [0, 10, 15, 20],
    [10, 0, 35, 25],
    [15, 35, 0, 30],
    [20, 25, 30, 0],
]

CITY_5 = [
    [0, 2, 9, 10, 7],
    [2, 0, 6, 4, 3],
    [9, 6, 0, 8, 5],
    [10, 4, 8, 0, 6],
    [7, 3, 5, 6, 0],
]

CITIES = [
    CITY_4,
    CITY_5,
]
OPTIMAL_DISTANCES = [
    80.0,   # Optimal distance for CITY_4
    26.0,   # Optimal distance for CITY_5
]
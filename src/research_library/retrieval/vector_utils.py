from math import sqrt

def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or len(a) != len(b):
        raise ValueError("两个向量必须非空，且长度相同")

    dot_product = sum(x * y for x, y in zip(a, b))
    length_a = sqrt(sum(x * x for x in a))
    length_b = sqrt(sum(y * y for y in b))

    if length_a == 0 or length_b == 0:
        raise ValueError("不能比较全为 0 的向量")

    return dot_product / (length_a * length_b)


if __name__ == "__main__":
    print(cosine_similarity([1, 0], [1, 0]))  # 1.0：方向相同
    print(cosine_similarity([1, 0], [0, 1]))  # 0.0：方向不同

# Number of Frequency

# text = "apple banana apple orange banana apple apple apple banana orange"

# freq = {}

# for word in text.split():
#     freq[word] = freq.get(word, 0) + 1

# print(freq)

# from collections import Counter

# freq = Counter(text.split())
# print(freq)

# def add_item(lst):
#     lst.append(100)

# data = [1, 2, 3]
# add_item(data)

# print(data)  # [1, 2, 3, 100]

# def change(x):
#     x = x + 10

# num = 5
# change(num)

# print(num)  # still 5

class BankAccount:
    def __init__(self, owner, balance=0):
        self.owner = owner
        self.balance = balance

    def deposit(self, amount):
        self.balance += amount

    def withdraw(self, amount):
        if amount > self.balance:
            print("Insufficient balance")
        else:
            self.balance -= amount

    def show(self):
        print(self.owner, self.balance)


acc = BankAccount("Anmol", 1000)
acc.deposit(500)
acc.withdraw(1500)
acc.show()
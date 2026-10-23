#import <CoreBluetooth/CoreBluetooth.h>
#import <Foundation/Foundation.h>

/// Calls CoreBluetooth addService while returning any Objective-C assertion to Swift.
NS_ASSUME_NONNULL_BEGIN
FOUNDATION_EXPORT NSString * _Nullable OpenIPhoneUseTryAddService(
    CBPeripheralManager *manager,
    CBMutableService *service
);
NS_ASSUME_NONNULL_END

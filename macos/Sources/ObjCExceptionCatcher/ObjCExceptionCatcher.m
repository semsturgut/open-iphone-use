#import "ObjCExceptionCatcher.h"

NSString * _Nullable OpenIPhoneUseTryAddService(CBPeripheralManager *manager, CBMutableService *service) {
    @try {
        [manager addService:service];
        return nil;
    } @catch (NSException *exception) {
        NSString *reason = exception.reason ?: @"No exception reason provided";
        return [NSString stringWithFormat:@"%@ — %@", exception.name, reason];
    }
}
